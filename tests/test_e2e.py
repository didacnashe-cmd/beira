"""Extremo a extremo: cliente MCP real -> Beira (HTTP) -> MCP local falso (HTTP)."""

import json
import os
import socket
import threading
import time

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

from beira.app import create_app
from beira.config import Config

ROOT = "Compartido/pau"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Served:
    """Una app ASGI servida por uvicorn en un hilo."""

    def __init__(self, app):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            assert time.time() < deadline, "el servidor no arrancó"
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def fake_lab_server():
    """Imita a mcp-lab-server: devuelve lo que recibe para poder comprobarlo."""
    lab = FastMCP("lab", stateless_http=True, json_response=True)

    @lab.tool()
    def list_obsidian_notes(subfolder: str = "") -> str:
        return f"LIST {subfolder}"

    @lab.tool()
    def read_obsidian_note(note_path: str) -> str:
        return f"READ {note_path}"

    @lab.tool()
    def append_to_obsidian_note(note_path: str, content: str) -> str:
        return f"APPEND {note_path}"

    @lab.tool()
    def query_personal_db(sql_query: str) -> str:
        return f"SQL {sql_query}"

    @lab.tool()
    def run_bash_command(command: str) -> str:
        return f"BASH {command}"

    @lab.tool()
    async def slow() -> str:
        await anyio.sleep(5)
        return "tarde"

    @lab.tool()
    def big() -> str:
        return "x" * 50_000

    @lab.tool()
    def boom() -> str:
        raise RuntimeError("fallo interno")

    return lab.streamable_http_app()


@pytest.fixture(scope="module")
def lab():
    with Served(fake_lab_server()) as s:
        yield s


@pytest.fixture
def env(lab, tmp_path):
    cfg = Config(
        db_path=str(tmp_path / "beira.db"),
        upstream_url=f"{lab.url}/mcp",
        timeout=1.5,
        rate_limit=1000,
        max_response_bytes=10_000,
        tools_ttl=0,
    )
    app = create_app(cfg)
    with Served(app) as s:
        s.beira = app.state.beira
        s.store = s.beira.store
        s.app_cfg = cfg
        yield s


async def _with_session(env, token, fn):
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=10) as http:
        async with streamable_http_client(f"{env.url}/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


def tools_of(env, token) -> set[str]:
    async def fn(s):
        return {t.name for t in (await s.list_tools()).tools}

    return anyio.run(_with_session, env, token, fn)


def call(env, token, name, args=None):
    async def fn(s):
        r = await s.call_tool(name, args or {})
        return r.isError, "".join(c.text for c in r.content if c.type == "text")

    return anyio.run(_with_session, env, token, fn)


def post_initialize(env, headers):
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    }
    h = {"Accept": "application/json, text/event-stream", **headers}
    return httpx.post(f"{env.url}/mcp", json=body, headers=h)


# --- autenticación -----------------------------------------------------------

def test_healthz_is_open(env):
    r = httpx.get(f"{env.url}/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer beira_falso"}, {"Authorization": "Basic abc"}])
def test_mcp_requires_a_valid_token(env, headers):
    r = post_initialize(env, headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_valid_token_and_revocation(env):
    token = env.store.invite("pau", [])
    assert post_initialize(env, {"Authorization": f"Bearer {token}"}).status_code == 200
    env.store.revoke("pau")
    assert post_initialize(env, {"Authorization": f"Bearer {token}"}).status_code == 401


# --- lista blanca ------------------------------------------------------------

def test_closed_by_default(env):
    token = env.store.invite("pau", [])
    assert tools_of(env, token) == {"ping"}
    is_error, text = call(env, token, "read_obsidian_note", {"note_path": "a.md"})
    assert is_error and "READ" not in text


def test_only_whitelisted_tools_are_visible_and_callable(env):
    token = env.store.invite("pau", ["list_obsidian_notes", "read_obsidian_note"], ROOT)
    assert tools_of(env, token) == {"ping", "list_obsidian_notes", "read_obsidian_note"}
    is_error, text = call(env, token, "query_personal_db", {"sql_query": "select 1"})
    assert is_error and "SQL" not in text
    is_error, text = call(env, token, "no_existe")
    assert is_error


def test_each_contact_sees_only_their_own(env):
    pau = env.store.invite("pau", ["read_obsidian_note"], ROOT)
    marta = env.store.invite("marta", ["query_personal_db"])
    assert tools_of(env, pau) == {"ping", "read_obsidian_note"}
    assert tools_of(env, marta) == {"ping", "query_personal_db"}
    assert call(env, marta, "query_personal_db", {"sql_query": "select 1"}) == (False, "SQL select 1")


def test_denied_tools_are_never_exposed(env):
    token = env.store.invite("pau", ["run_bash_command"])
    assert tools_of(env, token) == {"ping"}
    is_error, text = call(env, token, "run_bash_command", {"command": "id"})
    assert is_error and "BASH" not in text


def test_ping(env):
    token = env.store.invite("pau", [])
    is_error, text = call(env, token, "ping")
    assert not is_error and "operativa" in text and "pau" in text


# --- aislamiento de rutas ----------------------------------------------------

def test_paths_are_confined_to_the_contact_folder(env):
    token = env.store.invite("pau", ["list_obsidian_notes", "read_obsidian_note", "append_to_obsidian_note"], ROOT)
    assert call(env, token, "list_obsidian_notes") == (False, f"LIST {ROOT}")
    assert call(env, token, "list_obsidian_notes", {"subfolder": "viajes"}) == (False, f"LIST {ROOT}/viajes")
    assert call(env, token, "read_obsidian_note", {"note_path": "a.md"}) == (False, f"READ {ROOT}/a.md")
    assert call(env, token, "read_obsidian_note", {"note_path": f"{ROOT}/a.md"}) == (False, f"READ {ROOT}/a.md")
    assert call(env, token, "read_obsidian_note", {"note_path": "Privado/diario.md"}) == (
        False, f"READ {ROOT}/Privado/diario.md")
    assert call(env, token, "append_to_obsidian_note", {"note_path": "n.md", "content": "hola"}) == (
        False, f"APPEND {ROOT}/n.md")


@pytest.mark.parametrize("path", ["../Privado/diario.md", f"{ROOT}/../marta/a.md", "/etc/passwd", "C:\\x", "a/..\\..\\b"])
def test_path_escapes_never_reach_the_local_mcp(env, path):
    token = env.store.invite("pau", ["read_obsidian_note"], ROOT)
    is_error, text = call(env, token, "read_obsidian_note", {"note_path": path})
    assert is_error and "READ" not in text
    assert env.store.audit_log(1)[0]["outcome"] == "denied"


def test_path_tool_without_folder_is_not_exposed(env):
    token = env.store.invite("pau", ["read_obsidian_note"])
    assert tools_of(env, token) == {"ping"}
    is_error, text = call(env, token, "read_obsidian_note", {"note_path": "a.md"})
    assert is_error and "READ" not in text


# --- reglas por argumento ----------------------------------------------------

def test_rules_restrict_sql_tables_per_contact(env):
    # rules.json junto a la base de datos; se lee sin reiniciar Beira.
    rules_path = env.beira.cfg.rules.path
    with open(rules_path, "w", encoding="utf-8") as fh:
        json.dump({"pau": {"query_personal_db": {"tables": ["garmin_daily"]}}}, fh)
    pau = env.store.invite("pau", ["query_personal_db"])
    marta = env.store.invite("marta", ["query_personal_db"])

    ok = "SELECT day, steps FROM garmin_daily"
    assert call(env, pau, "query_personal_db", {"sql_query": ok}) == (False, f"SQL {ok}")
    for sql in [
        "SELECT * FROM hevy_sets",
        "SELECT * FROM garmin_daily JOIN cardio_activities",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM garmin_daily; ATTACH DATABASE 'x' AS x",
        "SELECT * FROM/**/\"HEVY_SETS\"",
    ]:
        is_error, text = call(env, pau, "query_personal_db", {"sql_query": sql})
        assert is_error and "SQL" not in text, sql
        assert env.store.audit_log(1)[0]["outcome"] == "denied"
    # Quien no tiene regla sigue como siempre.
    assert call(env, marta, "query_personal_db", {"sql_query": "SELECT * FROM hevy_sets"}) == (
        False, "SQL SELECT * FROM hevy_sets")

    # Un rules.json roto cierra todo (salvo ping) hasta que se arregle.
    with open(rules_path, "w", encoding="utf-8") as fh:
        fh.write("{ roto")
    os.utime(rules_path, ns=(1, 1))
    is_error, text = call(env, marta, "query_personal_db", {"sql_query": "SELECT 1"})
    assert is_error and "SQL" not in text
    assert call(env, marta, "ping")[0] is False
    os.remove(rules_path)


# --- límites -----------------------------------------------------------------

def test_timeout(env):
    token = env.store.invite("pau", ["slow"])
    start = time.monotonic()
    is_error, text = call(env, token, "slow")
    assert is_error and "tarde" not in text
    assert time.monotonic() - start < 4
    assert env.store.audit_log(1)[0]["outcome"] == "timeout"


def test_response_size_is_capped(env):
    token = env.store.invite("pau", ["big"])
    is_error, text = call(env, token, "big")
    assert not is_error
    assert len(text) < 10_200 and "recortada" in text
    assert env.store.audit_log(1)[0]["detail"] == "truncated"


def test_rate_limit_per_contact(env):
    env.beira.limiter.per_minute = 3
    pau = env.store.invite("pau", [])
    marta = env.store.invite("marta", [])
    results = [call(env, pau, "ping")[0] for _ in range(5)]
    assert results == [False, False, False, True, True]
    assert call(env, marta, "ping")[0] is False
    assert env.store.audit_log(1, "pau")[0]["outcome"] == "rate_limited"


# --- auditoría y resiliencia -------------------------------------------------

def test_audit_records_every_call(env):
    token = env.store.invite("pau", ["read_obsidian_note", "boom"], ROOT)
    call(env, token, "read_obsidian_note", {"note_path": "a.md"})
    call(env, token, "boom")
    call(env, token, "query_personal_db", {"sql_query": "select 1"})
    rows = list(reversed(env.store.audit_log(10)))
    assert [(r["contact"], r["tool"], r["outcome"]) for r in rows] == [
        ("pau", "read_obsidian_note", "ok"),
        ("pau", "boom", "tool_error"),
        ("pau", "query_personal_db", "denied"),
    ]
    # Se audita la ruta real que llegó al MCP local.
    assert f"{ROOT}/a.md" in rows[0]["args"]


def test_survives_local_mcp_being_down(tmp_path):
    cfg = Config(db_path=str(tmp_path / "b.db"), upstream_url=f"http://127.0.0.1:{free_port()}/mcp", timeout=5, connect_timeout=0.5)
    app = create_app(cfg)
    with Served(app) as env:
        env.store = app.state.beira.store
        token = env.store.invite("pau", ["read_obsidian_note"], ROOT)
        assert tools_of(env, token) == {"ping"}
        assert call(env, token, "ping")[0] is False
        is_error, text = call(env, token, "read_obsidian_note", {"note_path": "a.md"})
        assert is_error and "127.0.0.1" not in text  # sin detalles internos
        assert env.store.audit_log(1)[0]["outcome"] == "upstream_error"
        assert httpx.get(f"{env.url}/healthz").status_code == 200


# --- robustez ----------------------------------------------------------------

BAD = {"Authorization": "Bearer beira_falso", "CF-Connecting-IP": "203.0.113.7"}


@pytest.fixture
def behind_tunnel(lab, tmp_path):
    cfg = Config(
        db_path=str(tmp_path / "t.db"),
        upstream_url=f"{lab.url}/mcp",
        auth_fail_limit=5,
        trust_proxy_header="CF-Connecting-IP",
    )
    app = create_app(cfg)
    with Served(app) as s:
        s.store = app.state.beira.store
        yield s


def test_bad_token_burst_gets_429_without_hitting_sqlite(behind_tunnel, monkeypatch):
    env = behind_tunnel
    assert [post_initialize(env, BAD).status_code for _ in range(5)] == [401] * 5
    calls = []
    monkeypatch.setattr(env.store, "authenticate", lambda t: calls.append(t))
    r = post_initialize(env, BAD)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert calls == []  # bloqueada antes de consultar SQLite
    assert httpx.get(f"{env.url}/healthz", headers=BAD).status_code == 200


def test_good_token_from_another_ip_still_passes(behind_tunnel):
    env = behind_tunnel
    token = env.store.invite("pau", [])
    for _ in range(5):
        post_initialize(env, BAD)
    assert post_initialize(env, BAD).status_code == 429
    ok = {"Authorization": f"Bearer {token}", "CF-Connecting-IP": "198.51.100.9"}
    assert post_initialize(env, ok).status_code == 200
    assert call_with_headers(env, token, {"CF-Connecting-IP": "198.51.100.9"}) == (False, True)


def call_with_headers(env, token, extra):
    """Sesión MCP completa con cabeceras extra; devuelve (is_error del ping, sesión ok)."""
    async def run():
        headers = {"Authorization": f"Bearer {token}", **extra}
        async with httpx.AsyncClient(headers=headers, timeout=10) as http:
            async with streamable_http_client(f"{env.url}/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("ping", {})
                    return r.isError, True

    return anyio.run(run)


def test_proxy_header_is_ignored_unless_configured(env):
    # Sin BEIRA_TRUST_PROXY_HEADER cuenta la IP del socket: rotar la cabecera no esquiva el freno.
    for i in range(env.app_cfg.auth_fail_limit):
        post_initialize(env, {"Authorization": "Bearer beira_falso", "CF-Connecting-IP": f"10.0.0.{i}"})
    assert post_initialize(env, {"CF-Connecting-IP": "10.9.9.9"}).status_code == 429


def test_local_mcp_down_fails_fast_on_second_call(tmp_path):
    cfg = Config(
        db_path=str(tmp_path / "d.db"), upstream_url=f"http://127.0.0.1:{free_port()}/mcp",
        timeout=5, connect_timeout=0.5, upstream_backoff=30,
    )
    app = create_app(cfg)
    with Served(app) as env:
        env.store = app.state.beira.store
        token = env.store.invite("pau", ["query_personal_db"])
        assert call(env, token, "query_personal_db", {"sql_query": "select 1"})[0] is True
        assert app.state.beira.upstream.down.active()
        start = time.monotonic()
        is_error, _ = call(env, token, "query_personal_db", {"sql_query": "select 1"})
        assert is_error and time.monotonic() - start < 0.45  # sin esperar al connect_timeout
        assert env.store.audit_log(1)[0]["outcome"] == "upstream_error"
        assert tools_of(env, token) == {"ping"}


def test_slow_tool_does_not_mark_local_mcp_down(env):
    token = env.store.invite("pau", ["slow"])
    assert call(env, token, "slow")[0] is True
    assert env.store.audit_log(1)[0]["outcome"] == "timeout"
    assert not env.beira.upstream.down.active()
