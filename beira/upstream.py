"""Cliente del MCP local (mcp-lab-server) que Beira re-expone."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import anyio
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from .config import Config
from .limits import Cooldown

T = TypeVar("T")


class UpstreamError(Exception):
    """El MCP local falló o no está disponible."""


@asynccontextmanager
async def _clean_errors() -> AsyncIterator[None]:
    # El cliente MCP envuelve los fallos en ExceptionGroup anidados: se deja la causa.
    try:
        yield
    except BaseExceptionGroup as group:
        cause: BaseException = group
        while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
            cause = cause.exceptions[0]
        raise UpstreamError(f"{type(cause).__name__}: {cause}") from cause


def _is_down(exc: BaseException) -> bool:
    """¿El fallo dice que el MCP local está caído? Un timeout de lectura no: lento no es caído."""
    cause = exc.__cause__ if isinstance(exc, UpstreamError) else exc
    if isinstance(cause, httpx.ConnectTimeout):
        return True
    if isinstance(cause, (TimeoutError, httpx.TimeoutException)):
        return False
    return isinstance(cause, (OSError, httpx.TransportError))


class Upstream:
    def __init__(self, cfg: Config, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self._tools: list[types.Tool] | None = None
        self._tools_at = 0.0
        # Tras un fallo de conexión, durante unos segundos se falla al instante.
        self.down = Cooldown(cfg.upstream_backoff, clock)

    async def _guarded(self, op: Callable[[], Awaitable[T]]) -> T:
        if self.down.active():
            raise UpstreamError(self.down.reason)
        try:
            return await op()
        except Exception as exc:
            if _is_down(exc):
                self.down.trip(str(exc) if isinstance(exc, UpstreamError) else f"{type(exc).__name__}: {exc}")
            raise

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        # Una conexión por operación: sin sesiones caducadas que gestionar.
        headers = {}
        if self.cfg.upstream_token:
            headers["Authorization"] = f"Bearer {self.cfg.upstream_token}"
        timeout = httpx.Timeout(self.cfg.timeout, connect=self.cfg.connect_timeout)
        async with httpx.AsyncClient(headers=headers, timeout=timeout) as http:
            async with streamable_http_client(self.cfg.upstream_url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    try:
                        with anyio.fail_after(self.cfg.connect_timeout):
                            await session.initialize()
                    except TimeoutError:
                        # Caído no es lo mismo que lento: se avisa pronto y distinto.
                        raise ConnectionError("el MCP local no responde") from None
                    yield session

    async def list_tools(self) -> list[types.Tool]:
        """Catálogo de tools del MCP local, con caché breve."""
        if self._tools is not None and time.monotonic() - self._tools_at < self.cfg.tools_ttl:
            return self._tools
        tools = await self._guarded(self._fetch_tools)
        self._tools, self._tools_at = tools, time.monotonic()
        return tools

    async def _fetch_tools(self) -> list[types.Tool]:
        tools: list[types.Tool] = []
        with anyio.fail_after(self.cfg.timeout):
            async with _clean_errors(), self._session() as session:
                cursor = None
                while True:
                    page = await session.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    cursor = page.nextCursor
                    if not cursor:
                        break
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """Llama a una tool. Lanza TimeoutError si supera el tiempo máximo."""

        async def op() -> types.CallToolResult:
            with anyio.fail_after(self.cfg.timeout):
                async with _clean_errors(), self._session() as session:
                    return await session.call_tool(name, arguments)

        return await self._guarded(op)
