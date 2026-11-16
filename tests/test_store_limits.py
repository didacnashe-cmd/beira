import sqlite3

import pytest
from mcp import types

from beira.config import Config
from beira.limits import RateLimiter, cap_result
from beira.store import Store, StoreError, hash_token


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "sub" / "beira.db"))
    s.init()
    return s


def test_invite_and_authenticate(store):
    token = store.invite("pau", ["read_obsidian_note", "list_obsidian_notes"], "Compartido/pau/")
    assert token.startswith("beira_")
    c = store.authenticate(token)
    assert c.name == "pau"
    assert c.path_root == "Compartido/pau"
    assert c.tools == ("list_obsidian_notes", "read_obsidian_note")


def test_token_is_not_stored_in_clear(store):
    token = store.invite("pau", [])
    con = sqlite3.connect(store.path)
    dump = "\n".join(str(r) for r in con.execute("SELECT * FROM contacts"))
    con.close()
    assert token not in dump
    assert hash_token(token) in dump


def test_bad_tokens_are_rejected(store):
    store.invite("pau", [])
    assert store.authenticate("") is None
    assert store.authenticate("beira_falso") is None


def test_revoke_is_immediate_and_reinvite_rotates(store):
    old = store.invite("pau", ["a"], "Compartido/pau")
    assert store.revoke("pau") is True
    assert store.revoke("pau") is False
    assert store.authenticate(old) is None
    new = store.invite("pau", ["b"])
    assert store.authenticate(old) is None
    c = store.authenticate(new)
    assert c.tools == ("b",) and c.path_root is None


def test_invite_validation(store):
    store.invite("pau", [])
    with pytest.raises(StoreError):
        store.invite("pau", [])
    for bad in ["", "Pau", "pau garcia", "../x", "a" * 33]:
        with pytest.raises(StoreError):
            store.invite(bad, [])
    with pytest.raises(Exception):
        store.invite("marta", [], "../fuera")
    assert [c.name for c in store.contacts()] == ["pau"]


def test_audit_log(store):
    store.audit("pau", "read", {"note_path": "a.md"}, 12, "ok")
    store.audit("pau", "read", {"content": "x" * 5000}, 3, "denied", "motivo")
    store.audit("marta", "read", None, 1, "ok")
    rows = store.audit_log(10, "pau")
    assert [r["outcome"] for r in rows] == ["denied", "ok"]
    assert len(rows[0]["args"]) <= 2001
    assert len(store.audit_log(10)) == 3


def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(2, clock=lambda: now[0])
    assert rl.allow("pau") and rl.allow("pau")
    assert not rl.allow("pau")
    assert rl.allow("marta")  # cada contacto tiene su cupo
    now[0] = 60.0
    assert rl.allow("pau")
    assert RateLimiter(0).allow("x")  # 0 = sin límite


def _text(s):
    return types.TextContent(type="text", text=s)


def test_cap_result_keeps_small_results():
    r = types.CallToolResult(content=[_text("hola")], structuredContent={"a": 1})
    out, truncated = cap_result(r, 100)
    assert out is r and not truncated


def test_cap_result_truncates():
    r = types.CallToolResult(content=[_text("ñ" * 100), _text("resto")], structuredContent={"a": "x" * 500})
    out, truncated = cap_result(r, 51)
    assert truncated
    assert out.content[0].text == "ñ" * 25  # no parte un carácter multibyte
    assert out.content[1].text == ""
    assert "recortada" in out.content[-1].text
    assert out.structuredContent is None


def test_config_from_env():
    cfg = Config.from_env({"BEIRA_PORT": "9000", "BEIRA_PATH_ARGS": "t=p, u=q", "BEIRA_DENY_TOOLS": "x,y"})
    assert cfg.port == 9000
    assert cfg.path_args == {"t": "p", "u": "q"}
    assert cfg.deny_tools == {"x", "y"}
    default = Config.from_env({})
    assert default.host == "127.0.0.1" and "run_bash_command" in default.deny_tools
    assert default.path_args["read_obsidian_note"] == "note_path"
    with pytest.raises(ValueError):
        Config.from_env({"BEIRA_PATH_ARGS": "sin_igual"})
