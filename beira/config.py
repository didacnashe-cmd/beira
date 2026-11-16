"""Configuración por variables de entorno. Todo tiene un valor por defecto seguro."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("beira")

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


RULE_KEYS = {"tables", "argument"}


def parse_rules(data: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Valida el contenido de `BEIRA_RULES`. Cualquier cosa inesperada -> ValueError.

    Formato: {contacto: {tool: {"tables": [...], "argument": "sql_query"}}}.
    """
    if not isinstance(data, dict):
        raise ValueError("BEIRA_RULES: se espera un objeto {contacto: {tool: regla}}")
    for contact, tools in data.items():
        if not isinstance(tools, dict):
            raise ValueError(f"BEIRA_RULES: las reglas de {contact!r} deben ser un objeto {{tool: regla}}")
        for tool, rule in tools.items():
            where = f"BEIRA_RULES[{contact!r}][{tool!r}]"
            if not isinstance(rule, dict):
                raise ValueError(f"{where}: la regla debe ser un objeto")
            unknown = set(rule) - RULE_KEYS
            if unknown:
                raise ValueError(f"{where}: claves desconocidas {sorted(unknown)}")
            tables = rule.get("tables")
            if not isinstance(tables, list) or not all(isinstance(t, str) and t for t in tables):
                raise ValueError(f"{where}: 'tables' debe ser una lista de nombres de tabla")
            if not isinstance(rule.get("argument", "sql_query"), str):
                raise ValueError(f"{where}: 'argument' debe ser un texto")
    return data


class Rules:
    """Reglas por argumento leídas de un JSON (`BEIRA_RULES`).

    Se releen al cambiar el fichero, sin reiniciar. Si no existe, no hay
    reglas. Si está mal formado, se lanza `Denied` en cada llamada (cerrado
    por defecto: no sabemos qué restricciones quería aplicar el dueño).
    """

    def __init__(self, path: str):
        self.path = path
        self._stamp: tuple[int, int] | None = None
        self._rules: dict[str, Any] = {}
        self._error: str | None = None
        self._lock = threading.Lock()
        try:  # un fichero mal formado impide arrancar: mejor un error visible
            self.get()
        except Exception:
            raise ValueError(f"BEIRA_RULES ({path}) no válido: {self._error}") from None

    def get(self) -> dict[str, Any]:
        with self._lock:
            try:
                st = os.stat(self.path)
            except FileNotFoundError:
                self._stamp, self._rules, self._error = None, {}, None
                return self._rules
            except OSError as exc:
                self._stamp, self._error = None, f"no se puede leer: {exc}"
            else:
                stamp = (st.st_mtime_ns, st.st_size)
                if stamp != self._stamp:
                    self._stamp = stamp
                    try:
                        with open(self.path, encoding="utf-8") as fh:
                            self._rules, self._error = parse_rules(json.load(fh)), None
                    except (OSError, ValueError) as exc:  # JSONDecodeError es ValueError
                        self._rules, self._error = {}, str(exc)
                        log.error("Reglas %s no válidas, se deniegan todas las tools: %s", self.path, exc)
            if self._error is not None:
                from .policy import Denied

                raise Denied("Servicio mal configurado. Avisa a quien te invitó.")
            return self._rules


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
    # Robustez: freno a tokens inválidos por IP y caché de «MCP local caído».
    auth_fail_limit: int = 20        # 401 por IP y minuto antes de responder 429 (0 = sin freno)
    trust_proxy_header: str = ""     # cabecera con la IP real (p. ej. CF-Connecting-IP); vacío = IP del socket
    upstream_backoff: float = 10.0   # segundos que se da por caído el MCP local tras un fallo de conexión
    # Reglas por argumento (JSON). Vacío: `rules.json` junto a la base de datos
    # (`/data/rules.json` en Docker). Si el fichero no existe, no hay reglas.
    rules_path: str = ""
    rules: Rules = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        path = self.rules_path or os.path.join(os.path.dirname(self.db_path), "rules.json")
        object.__setattr__(self, "rules", Rules(path))

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
            auth_fail_limit=int(e.get("BEIRA_AUTH_FAIL_LIMIT", d.auth_fail_limit)),
            trust_proxy_header=e.get("BEIRA_TRUST_PROXY_HEADER", d.trust_proxy_header).strip(),
            upstream_backoff=float(e.get("BEIRA_UPSTREAM_BACKOFF", d.upstream_backoff)),
            rules_path=e.get("BEIRA_RULES", d.rules_path),
        )
