"""Servidor Beira: autenticación por contacto + proxy filtrado del MCP local."""

from __future__ import annotations

import contextlib
import logging
import math
import time
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import VERSION
from .config import Config
from .limits import FailureGuard, RateLimiter, cap_result
from .policy import Denied, apply_path_policy
from .store import Contact, Store
from .upstream import Upstream

log = logging.getLogger("beira")

PING = types.Tool(
    name="ping",
    description="Comprueba que la embajada Beira responde.",
    inputSchema={"type": "object", "properties": {}},
)
PATH_NOTE = " (Las rutas son relativas a tu carpeta compartida.)"


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)


class Beira:
    def __init__(self, cfg: Config, store: Store | None = None, upstream: Upstream | None = None):
        self.cfg = cfg
        self.store = store or Store(cfg.db_path)
        self.store.init()
        self.upstream = upstream or Upstream(cfg)
        self.limiter = RateLimiter(cfg.rate_limit)

    def visible(self, contact: Contact, tool: str) -> bool:
        """¿Puede `contact` ver y llamar a `tool`? Cerrado por defecto."""
        if tool in self.cfg.deny_tools or tool not in contact.tools:
            return False
        if tool in self.cfg.path_args and not contact.path_root:
            return False  # tool con rutas, contacto sin carpeta: no se expone
        return True

    async def list_tools(self, contact: Contact) -> list[types.Tool]:
        tools = [PING]
        try:
            upstream = await self.upstream.list_tools()
        except Exception as exc:  # el MCP local caído no tumba Beira
            log.warning("No se pudo listar el MCP local: %s", exc)
            return tools
        for tool in upstream:
            if tool.name == PING.name or not self.visible(contact, tool.name):
                continue
            update: dict[str, Any] = {"outputSchema": None}
            if tool.name in self.cfg.path_args:
                update["description"] = (tool.description or "") + PATH_NOTE
            tools.append(tool.model_copy(update=update))
        return tools

    async def call_tool(self, contact: Contact, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        start = time.monotonic()
        outcome, detail = "ok", ""
        try:
            if not self.limiter.allow(contact.name):
                outcome = "rate_limited"
                return _error("Demasiadas llamadas. Espera un minuto y vuelve a intentarlo.")
            if name == PING.name:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"Beira {VERSION} operativa. Hola, {contact.name}.")]
                )
            try:
                if not self.visible(contact, name):
                    raise Denied("Tool no disponible.")
                arguments = apply_path_policy(name, arguments, self.cfg.path_args, contact.path_root)
            except Denied as exc:
                outcome, detail = "denied", str(exc)
                return _error(str(exc))
            try:
                result = await self.upstream.call_tool(name, arguments)
            except TimeoutError:
                outcome = "timeout"
                return _error(f"La tool tardó más de {self.cfg.timeout:g} s y se canceló.")
            except Exception as exc:
                outcome, detail = "upstream_error", str(exc)
                log.warning("Fallo del MCP local en %s: %s", name, exc)
                return _error("El servicio no está disponible ahora mismo.")
            result, truncated = cap_result(result, self.cfg.max_response_bytes)
            if result.isError:
                outcome = "tool_error"
            if truncated:
                detail = "truncated"
            return result
        finally:
            ms = int((time.monotonic() - start) * 1000)
            try:
                self.store.audit(contact.name, name, arguments, ms, outcome, detail)
            except Exception:
                log.exception("No se pudo escribir la auditoría")


class AuthMiddleware:
    """Exige `Authorization: Bearer <token>` de un contacto activo.

    Una IP con demasiados 401 seguidos recibe 429 durante un rato, sin tocar SQLite.
    """

    OPEN_PATHS = {"/healthz"}

    def __init__(self, app: ASGIApp, store: Store, guard: FailureGuard | None = None, ip_header: str = ""):
        self.app = app
        self.store = store
        self.guard = guard
        # Solo se confía en la cabecera si se configura (Beira detrás de un túnel).
        self.ip_header = ip_header.lower().encode("latin-1")

    def client_ip(self, scope: Scope, headers: dict[bytes, bytes]) -> str:
        if self.ip_header:
            forwarded = headers.get(self.ip_header, b"").decode("latin-1").strip()
            if forwarded:
                return forwarded
        client = scope.get("client")
        return client[0] if client else "?"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        ip = self.client_ip(scope, headers)
        if self.guard is not None:
            left = self.guard.blocked(ip)
            if left > 0:
                response = JSONResponse(
                    {"error": "too_many_auth_failures"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, math.ceil(left)))},
                )
                await response(scope, receive, send)
                return
        header = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = header.partition(" ")
        contact = self.store.authenticate(token.strip()) if scheme.lower() == "bearer" else None
        if contact is None:
            if self.guard is not None:
                self.guard.fail(ip)
            response = JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["contact"] = contact
        await self.app(scope, receive, send)


def create_app(cfg: Config | None = None, beira: Beira | None = None) -> Starlette:
    cfg = cfg or Config.from_env()
    beira = beira or Beira(cfg)
    server: Server = Server("beira", version=VERSION)

    def contact() -> Contact:
        return server.request_context.request.state.contact

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return await beira.list_tools(contact())

    # Sin validación local: el esquema lo valida el MCP local, que es su dueño.
    @server.call_tool(validate_input=False)
    async def _call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await beira.call_tool(contact(), name, arguments or {})

    # Sin estado: cada petición se autentica sola y revocar surte efecto al instante.
    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)

    class MCPEndpoint:  # una instancia (no una función) para que Starlette la trate como ASGI
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await manager.handle_request(scope, receive, send)

    async def healthz(_request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": VERSION})

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    app = Starlette(
        routes=[Route("/healthz", healthz), Route("/mcp", endpoint=MCPEndpoint())],
        lifespan=lifespan,
    )
    guard = FailureGuard(cfg.auth_fail_limit)
    app.add_middleware(AuthMiddleware, store=beira.store, guard=guard, ip_header=cfg.trust_proxy_header)
    app.state.beira = beira
    app.state.auth_guard = guard
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    print(f"Beira {VERSION} arrancando en {cfg.host}:{cfg.port} -> {cfg.upstream_url}", flush=True)
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
