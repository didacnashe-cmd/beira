"""Configuración por variables de entorno. Todo tiene un valor por defecto seguro."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_PATH_ARGS = (
    "list_obsidian_notes=subfolder,"
    "read_obsidian_note=note_path,"
    "append_to_obsidian_note=note_path"
)


def _parse_map(raw: str) -> dict[str, str]:
    """'tool=arg,tool2=arg2' -> {'tool': 'arg', 'tool2': 'arg2'}"""
    out: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        tool, sep, arg = item.partition("=")
        if not sep or not tool.strip() or not arg.strip():
            raise ValueError(f"BEIRA_PATH_ARGS mal formado: {item!r} (se espera tool=argumento)")
        out[tool.strip()] = arg.strip()
    return out


def _parse_set(raw: str) -> frozenset[str]:
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8100
    db_path: str = "data/beira.db"
    # MCP local que Beira re-expone (mcp-lab-server).
    upstream_url: str = "http://127.0.0.1:8000/mcp"
    upstream_token: str = ""
    # Límites.
    timeout: float = 30.0            # segundos por llamada al MCP local
    connect_timeout: float = 5.0     # segundos para conectar con el MCP local
    rate_limit: int = 30             # llamadas a tools por contacto y minuto
    max_response_bytes: int = 200_000
    tools_ttl: float = 60.0          # caché del catálogo de tools del MCP local
    # Qué argumento de cada tool es una ruta que hay que aislar por contacto.
    path_args: dict[str, str] = field(default_factory=lambda: _parse_map(DEFAULT_PATH_ARGS))
    # Tools que jamás se exponen, aunque alguien las ponga en una lista blanca.
    deny_tools: frozenset[str] = frozenset({"run_bash_command"})
    # URL pública (solo para imprimirla en las invitaciones).
    public_url: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env
        d = cls()
        return cls(
            host=e.get("BEIRA_HOST", d.host),
            port=int(e.get("BEIRA_PORT", d.port)),
            db_path=e.get("BEIRA_DB", d.db_path),
            upstream_url=e.get("BEIRA_UPSTREAM_URL", d.upstream_url),
            upstream_token=e.get("BEIRA_UPSTREAM_TOKEN", d.upstream_token),
            timeout=float(e.get("BEIRA_TIMEOUT", d.timeout)),
            connect_timeout=float(e.get("BEIRA_CONNECT_TIMEOUT", d.connect_timeout)),
            rate_limit=int(e.get("BEIRA_RATE_LIMIT", d.rate_limit)),
            max_response_bytes=int(e.get("BEIRA_MAX_RESPONSE_BYTES", d.max_response_bytes)),
            tools_ttl=float(e.get("BEIRA_TOOLS_TTL", d.tools_ttl)),
            path_args=_parse_map(e.get("BEIRA_PATH_ARGS", DEFAULT_PATH_ARGS)),
            deny_tools=_parse_set(e.get("BEIRA_DENY_TOOLS", ",".join(sorted(d.deny_tools)))),
            public_url=e.get("BEIRA_PUBLIC_URL", d.public_url),
        )
