"""CLI de administración: python -m beira <orden>."""

from __future__ import annotations

import argparse
import json
import sys

import anyio

from . import VERSION
from .config import Config
from .policy import Denied
from .store import Store, StoreError
from .upstream import Upstream


def _tools_arg(values: list[str] | None) -> list[str]:
    return [t for v in values or [] for t in v.split(",") if t.strip()]


def cmd_invite(cfg: Config, store: Store, args) -> int:
    tools = _tools_arg(args.tools)
    token = store.invite(args.name, tools, args.path)
    print(f"Contacto '{args.name}' creado.")
    print(f"  Tools:   {', '.join(tools) or '(solo ping)'}")
    print(f"  Carpeta: {args.path or '(ninguna)'}")
    for t in tools:
        if t in cfg.deny_tools:
            print(f"  AVISO: '{t}' está en BEIRA_DENY_TOOLS y no se expondrá nunca.")
        elif t in cfg.path_args and not args.path:
            print(f"  AVISO: '{t}' usa rutas y no has dado --path: no se expondrá.")
        elif t not in cfg.path_args and args.path:
            print(f"  AVISO: '{t}' no tiene argumento de ruta declarado: --path no la limita.")
    url = (cfg.public_url or f"http://{cfg.host}:{cfg.port}").rstrip("/") + "/mcp"
    print("\nToken (solo se muestra esta vez):\n")
    print(f"  {token}\n")
    print("Configuración para su cliente MCP (p. ej. Claude Desktop):\n")
    snippet = {
        "mcpServers": {
            "beira": {
                "command": "npx",
                "args": ["-y", "mcp-remote", url, "--header", f"Authorization: Bearer {token}"],
            }
        }
    }
    print(json.dumps(snippet, indent=2))
    return 0


def cmd_revoke(cfg: Config, store: Store, args) -> int:
    if store.revoke(args.name):
        print(f"Contacto '{args.name}' revocado. Su token ha dejado de funcionar.")
        return 0
    print(f"No hay ningún contacto activo llamado '{args.name}'.", file=sys.stderr)
    return 1


def cmd_list(cfg: Config, store: Store, args) -> int:
    contacts = store.contacts()
    if not contacts:
        print("No hay contactos. Crea uno con: python -m beira invite <nombre> --tools ... --path ...")
        return 0
    for c in contacts:
        state = "REVOCADO" if c.revoked_at else "activo"
        print(f"{c.name:<20} {state:<9} carpeta={c.path_root or '-'}  tools={', '.join(c.tools) or '-'}")
    return 0


def cmd_audit(cfg: Config, store: Store, args) -> int:
    for r in reversed(store.audit_log(args.limit, args.contact)):
        detail = f"  [{r['detail']}]" if r["detail"] else ""
        print(f"{r['ts']}  {r['contact']:<12} {r['outcome']:<14} {r['duration_ms']:>6} ms  {r['tool']} {r['args']}{detail}")
    return 0


def cmd_tools(cfg: Config, store: Store, args) -> int:
    try:
        tools = anyio.run(Upstream(cfg).list_tools)
    except Exception as exc:
        print(f"No se pudo conectar al MCP local en {cfg.upstream_url}: {exc}", file=sys.stderr)
        return 1
    for t in tools:
        marks = []
        if t.name in cfg.deny_tools:
            marks.append("BLOQUEADA")
        if t.name in cfg.path_args:
            marks.append(f"ruta={cfg.path_args[t.name]}")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        print(f"{t.name}{suffix}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="beira", description=f"Beira {VERSION}: embajada MCP.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("invite", help="crea un contacto y genera su token")
    p.add_argument("name")
    p.add_argument("--tools", action="append", help="tools permitidas, separadas por comas")
    p.add_argument("--path", help="carpeta de la bóveda a la que queda limitado (p. ej. Compartido/pau)")
    p.set_defaults(fn=cmd_invite)

    p = sub.add_parser("revoke", help="revoca el token de un contacto")
    p.add_argument("name")
    p.set_defaults(fn=cmd_revoke)

    p = sub.add_parser("list", help="lista los contactos y sus permisos")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("audit", help="muestra las últimas llamadas")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--contact")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("tools", help="lista las tools del MCP local")
    p.set_defaults(fn=cmd_tools)

    args = parser.parse_args(argv)
    cfg = Config.from_env()
    store = Store(cfg.db_path)
    store.init()
    try:
        return args.fn(cfg, store, args)
    except (StoreError, Denied) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
