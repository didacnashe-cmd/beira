"""Política de acceso: qué tools ve un contacto y a qué rutas puede llegar.

Todo cerrado por defecto: lo que no está permitido explícitamente, se deniega.
"""

from __future__ import annotations

import re
from typing import Any

_DRIVE = re.compile(r"^[A-Za-z]:")


class Denied(Exception):
    """La llamada no está permitida. El mensaje es apto para enseñárselo al contacto."""


def _split(value: str) -> list[str]:
    """Trocea una ruta relativa y rechaza cualquier intento de salirse."""
    if "\x00" in value:
        raise Denied("Ruta no válida.")
    v = value.replace("\\", "/")
    if v.startswith(("/", "~")) or _DRIVE.match(v):
        raise Denied("Solo se admiten rutas relativas a tu carpeta compartida.")
    parts = [p for p in v.split("/") if p not in ("", ".")]
    if any(p.strip() == ".." for p in parts):
        raise Denied("No se admite '..' en las rutas.")
    return parts


def normalize_root(root: str) -> str:
    """Valida la carpeta raíz de un contacto (p. ej. 'Compartido/pau')."""
    parts = _split(root)
    if not parts:
        raise Denied("La carpeta del contacto no puede estar vacía.")
    return "/".join(parts)


def confine(value: Any, root: str) -> str:
    """Devuelve `value` reescrita para que quede siempre dentro de `root`.

    Acepta tanto rutas relativas a la carpeta del contacto ('notas/a.md')
    como las que ya traen el prefijo ('Compartido/pau/notas/a.md'), que es
    como las devuelve el listado del MCP local.
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Denied("La ruta debe ser un texto.")
    parts = _split(value)
    root_parts = root.split("/")
    if parts[: len(root_parts)] == root_parts:
        parts = parts[len(root_parts):]
    return "/".join(root_parts + parts)


def apply_path_policy(
    tool: str,
    arguments: dict[str, Any],
    path_args: dict[str, str],
    path_root: str | None,
) -> dict[str, Any]:
    """Aísla el argumento de ruta de `tool`, si lo tiene.

    Una tool con argumento de ruta exige que el contacto tenga carpeta; sin
    ella se deniega (nunca se deja pasar la ruta tal cual).
    """
    arg = path_args.get(tool)
    if arg is None:
        return arguments
    if not path_root:
        raise Denied("Tool no disponible.")
    out = dict(arguments)
    out[arg] = confine(out.get(arg), path_root)
    return out
