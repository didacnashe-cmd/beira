"""Freno a tokens inválidos y caché de «MCP local caído», con reloj falso."""

import anyio
import httpx
import pytest

from beira.config import Config
from beira.limits import Cooldown, FailureGuard
from beira.upstream import Upstream, UpstreamError


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- FailureGuard ------------------------------------------------------------

def test_guard_blocks_after_limit_and_releases():
    clock = Clock()
    g = FailureGuard(3, window=60, block_for=30, clock=clock)
    for _ in range(2):
        g.fail("1.1.1.1")
    assert g.blocked("1.1.1.1") == 0
    g.fail("1.1.1.1")
    assert g.blocked("1.1.1.1") == 30
    clock.now += 29
    assert g.blocked("1.1.1.1") == 1
    clock.now += 1
    assert g.blocked("1.1.1.1") == 0


def test_guard_window_slides():
    clock = Clock()
    g = FailureGuard(3, window=60, clock=clock)
    g.fail("ip")
    g.fail("ip")
    clock.now += 60  # los dos primeros caducan
    g.fail("ip")
    g.fail("ip")
    assert g.blocked("ip") == 0
    g.fail("ip")
    assert g.blocked("ip") > 0


def test_guard_is_per_key_and_can_be_disabled():
    clock = Clock()
    g = FailureGuard(2, clock=clock)
    g.fail("a")
    g.fail("a")
    assert g.blocked("a") > 0 and g.blocked("b") == 0
    off = FailureGuard(0, clock=clock)
    for _ in range(100):
        off.fail("a")
    assert off.blocked("a") == 0


def test_guard_sweeps_stale_keys():
    clock = Clock()
    g = FailureGuard(5, window=60, clock=clock)
    g.MAX_KEYS = 10
    for i in range(10):
        g.fail(f"ip{i}")
    clock.now += 61
    g.fail("nueva")
    assert set(g._fails) == {"nueva"}


# --- Cooldown ----------------------------------------------------------------

def test_cooldown():
    clock = Clock()
    c = Cooldown(10, clock)
    assert not c.active()
    c.trip("caído")
    assert c.active() and c.reason == "caído"
    clock.now += 9.9
    assert c.active()
    clock.now += 0.1
    assert not c.active()
    c.trip("otra vez")
    c.reset()
    assert not c.active()
    zero = Cooldown(0, clock)
    zero.trip("x")
    assert not zero.active()


# --- Upstream con el MCP local caído ----------------------------------------

class FlakyUpstream(Upstream):
    """Upstream cuyo `_session` falla como se le diga, contando intentos."""

    def __init__(self, cfg, clock, error):
        super().__init__(cfg, clock)
        self.error = error
        self.attempts = 0

    def _session(self):
        self.attempts += 1
        raise self.error


def _cfg(**kw):
    return Config(upstream_backoff=10, tools_ttl=0, **kw)


def test_connection_failure_short_circuits_for_a_while():
    clock = Clock()
    up = FlakyUpstream(_cfg(), clock, ConnectionError("el MCP local no responde"))

    async def run():
        with pytest.raises(ConnectionError):
            await up.call_tool("x", {})
        with pytest.raises(UpstreamError, match="no responde"):
            await up.call_tool("x", {})
        with pytest.raises(UpstreamError):
            await up.list_tools()
        assert up.attempts == 1
        clock.now += 10
        with pytest.raises(ConnectionError):
            await up.call_tool("x", {})
        assert up.attempts == 2

    anyio.run(run)


@pytest.mark.parametrize("error", [TimeoutError(), httpx.ReadTimeout("lento"), RuntimeError("otra cosa")])
def test_slow_or_other_errors_do_not_trip(error):
    up = FlakyUpstream(_cfg(), Clock(), error)

    async def run():
        for _ in range(2):
            with pytest.raises(type(error)):
                await up.call_tool("x", {})
        assert up.attempts == 2 and not up.down.active()

    anyio.run(run)


def test_upstream_error_with_connection_cause_trips():
    err = UpstreamError("ConnectError: refused")
    err.__cause__ = httpx.ConnectError("refused")
    up = FlakyUpstream(_cfg(), Clock(), err)

    async def run():
        with pytest.raises(UpstreamError):
            await up.call_tool("x", {})
        with pytest.raises(UpstreamError, match="refused"):
            await up.call_tool("x", {})
        assert up.attempts == 1

    anyio.run(run)


def test_config_from_env():
    cfg = Config.from_env({
        "BEIRA_AUTH_FAIL_LIMIT": "5",
        "BEIRA_TRUST_PROXY_HEADER": " CF-Connecting-IP ",
        "BEIRA_UPSTREAM_BACKOFF": "2.5",
    })
    assert (cfg.auth_fail_limit, cfg.trust_proxy_header, cfg.upstream_backoff) == (5, "CF-Connecting-IP", 2.5)
    d = Config.from_env({})
    assert (d.auth_fail_limit, d.trust_proxy_header, d.upstream_backoff) == (20, "", 10.0)
