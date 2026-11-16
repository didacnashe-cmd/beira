"""Gestión de permisos sin revocar (grant/ungrant/path/rotate) y purga de auditoría."""

import re
import sqlite3

import pytest

from beira import cli
from beira.policy import Denied
from beira.store import Store, StoreError


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "beira.db"))
    s.init()
    return s


# --- Store ----------------------------------------------------------------


def test_grant_adds_tools_and_keeps_token(store):
    token = store.invite("pau", ["read_obsidian_note"], "Compartido/pau")
    c = store.grant("pau", ["list_obsidian_notes", "read_obsidian_note", " "])
    assert c.tools == ("list_obsidian_notes", "read_obsidian_note")
    assert store.authenticate(token).tools == c.tools
    assert c.path_root == "Compartido/pau"


def test_ungrant_removes_tools(store):
    store.invite("pau", ["a", "b", "c"])
    c = store.ungrant("pau", ["a", "c", "no_estaba"])
    assert c.tools == ("b",)


def test_grant_and_ungrant_need_tools(store):
    store.invite("pau", [])
    with pytest.raises(StoreError):
        store.grant("pau", [])
    with pytest.raises(StoreError):
        store.ungrant("pau", [" "])


def test_set_path_validates_and_clears(store):
    token = store.invite("pau", ["read_obsidian_note"])
    assert store.set_path("pau", "Compartido/x/").path_root == "Compartido/x"
    assert store.authenticate(token).path_root == "Compartido/x"
    with pytest.raises(Denied):
        store.set_path("pau", "../fuera")
    assert store.contact("pau").path_root == "Compartido/x"
    assert store.set_path("pau", None).path_root is None


def test_rotate_invalidates_old_token_and_keeps_permissions(store):
    old = store.invite("pau", ["a", "b"], "Compartido/pau")
    new = store.rotate("pau")
    assert new != old and new.startswith("beira_")
    assert store.authenticate(old) is None
    c = store.authenticate(new)
    assert c.tools == ("a", "b") and c.path_root == "Compartido/pau"


@pytest.mark.parametrize("op", ["grant", "ungrant", "path", "rotate", "contact"])
def test_unknown_or_revoked_contact_is_an_error(store, op):
    store.invite("pau", ["a"])
    store.revoke("pau")
    calls = {
        "grant": lambda n: store.grant(n, ["a"]),
        "ungrant": lambda n: store.ungrant(n, ["a"]),
        "path": lambda n: store.set_path(n, "Compartido/x"),
        "rotate": store.rotate,
        "contact": store.contact,
    }
    with pytest.raises(StoreError, match="revocado"):
        calls[op]("pau")
    with pytest.raises(StoreError, match="No existe"):
        calls[op]("nadie")


def test_purge_audit_before_date(store):
    store.audit("pau", "read", None, 1, "ok")
    con = sqlite3.connect(store.path)
    with con:
        con.executemany(
            "INSERT INTO audit (ts, contact, tool, args, duration_ms, outcome) VALUES (?, 'pau', 't', '{}', 1, 'ok')",
            [("2025-12-31T23:59:59+00:00",), ("2026-01-01T00:00:00+00:00",), ("2025-06-01T10:00:00+00:00",)],
        )
    con.close()
    assert store.purge_audit("2026-01-01") == 2
    assert sorted(r["ts"][:10] for r in store.audit_log(10))[0] == "2026-01-01"
    assert store.purge_audit("2026-01-01") == 0
    assert len(store.audit_log(10)) == 2
    with pytest.raises(StoreError):
        store.purge_audit("01/01/2026")


# --- CLI ------------------------------------------------------------------


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BEIRA_DB", str(tmp_path / "cli.db"))
    monkeypatch.setenv("BEIRA_PATH_ARGS", "read_obsidian_note=note_path,list_obsidian_notes=subfolder")
    monkeypatch.setenv("BEIRA_DENY_TOOLS", "run_bash_command")
    monkeypatch.setenv("BEIRA_PUBLIC_URL", "https://beira.example")

    def _run(*argv):
        code = cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    _run.store = lambda: Store(str(tmp_path / "cli.db"))
    return _run


def _token(out: str) -> str:
    return re.search(r"beira_[A-Za-z0-9_-]+", out).group(0)


def test_cli_grant_and_ungrant(run):
    assert run("invite", "pau", "--tools", "read_obsidian_note", "--path", "Compartido/pau")[0] == 0
    code, out, _ = run("grant", "pau", "--tools", "list_obsidian_notes,get_time")
    assert code == 0
    assert "list_obsidian_notes" in out
    assert "'get_time' no tiene argumento de ruta" in out
    assert run.store().contact("pau").tools == ("get_time", "list_obsidian_notes", "read_obsidian_note")

    code, out, _ = run("ungrant", "pau", "--tools", "get_time,otra")
    assert code == 0
    assert "'otra' no estaba concedida" in out
    assert run.store().contact("pau").tools == ("list_obsidian_notes", "read_obsidian_note")


def test_cli_grant_warns_like_invite(run):
    run("invite", "pau", "--tools", "get_time")
    _, out, _ = run("grant", "pau", "--tools", "run_bash_command,read_obsidian_note")
    assert "'run_bash_command' está en BEIRA_DENY_TOOLS" in out
    assert "'read_obsidian_note' usa rutas y no has dado --path" in out


def test_cli_path_change_and_clear(run):
    run("invite", "pau", "--tools", "read_obsidian_note,get_time", "--path", "Compartido/pau")
    code, out, _ = run("path", "pau", "--path", "Compartido/x/")
    assert code == 0
    assert run.store().contact("pau").path_root == "Compartido/x"
    assert "'get_time' no tiene argumento de ruta" in out

    code, out, _ = run("path", "pau", "--clear")
    assert code == 0
    assert run.store().contact("pau").path_root is None
    assert "'read_obsidian_note' usa rutas y no has dado --path" in out

    code, _, err = run("path", "pau", "--path", "../fuera")
    assert code == 1 and "Error:" in err


def test_cli_path_requires_path_or_clear(run):
    run("invite", "pau", "--tools", "get_time")
    with pytest.raises(SystemExit):
        run("path", "pau")
    with pytest.raises(SystemExit):
        run("path", "pau", "--path", "Compartido/x", "--clear")


def test_cli_rotate_prints_new_token_and_config(run):
    _, out, _ = run("invite", "pau", "--tools", "read_obsidian_note", "--path", "Compartido/pau")
    old = _token(out)
    code, out, _ = run("rotate", "pau")
    assert code == 0
    new = _token(out)
    assert new != old
    assert '"mcpServers"' in out and "https://beira.example/mcp" in out
    assert f"Authorization: Bearer {new}" in out
    s = run.store()
    assert s.authenticate(old) is None
    c = s.authenticate(new)
    assert c.tools == ("read_obsidian_note",) and c.path_root == "Compartido/pau"


@pytest.mark.parametrize(
    "argv",
    [
        ("grant", "{n}", "--tools", "a"),
        ("ungrant", "{n}", "--tools", "a"),
        ("path", "{n}", "--path", "Compartido/x"),
        ("path", "{n}", "--clear"),
        ("rotate", "{n}"),
    ],
)
def test_cli_unknown_or_revoked_contact_exits_1(run, argv):
    run("invite", "pau", "--tools", "a")
    run("revoke", "pau")
    for name, msg in [("pau", "revocado"), ("nadie", "No existe")]:
        code, out, err = run(*[a.format(n=name) for a in argv])
        assert code == 1
        assert msg in err
        assert "beira_" not in out


def test_cli_audit_purge(run):
    s = run.store()
    s.init()
    s.audit("pau", "read", None, 1, "ok")
    con = sqlite3.connect(s.path)
    with con:
        con.execute(
            "INSERT INTO audit (ts, contact, tool, args, duration_ms, outcome) "
            "VALUES ('2025-01-01T00:00:00+00:00', 'pau', 't', '{}', 1, 'ok')"
        )
    con.close()
    code, out, _ = run("audit", "--purge-before", "2026-01-01")
    assert code == 0 and "1 fila(s)" in out
    assert len(s.audit_log(10)) == 1

    code, _, err = run("audit", "--purge-before", "ayer")
    assert code == 1 and "AAAA-MM-DD" in err
