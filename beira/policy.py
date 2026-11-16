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


# --- Reglas por argumento ------------------------------------------------------
#
# Las tools sin argumento de ruta (p. ej. `query_personal_db`) se pueden acotar
# por contacto con reglas (ver `beira.config.Rules`). Hoy hay un tipo de regla:
# `tables`, que limita las tablas que puede leer una consulta SQL.
#
# El análisis es deliberadamente conservador: ante cualquier cosa que no se
# entienda, se deniega. Es preferible rechazar una consulta válida a dejar
# pasar una que lea otra tabla.

_SQL_WS = " \t\n\f\r"
_SQL_PUNCT = set("+-*/%<>=!|&~,.();")
# Nunca caben en una consulta de solo lectura, estén donde estén (SQLite
# admite `WITH ... INSERT/UPDATE/DELETE`), y funciones que tocan ficheros.
_SQL_FORBIDDEN_WORDS = {
    "alter", "analyze", "attach", "begin", "commit", "create", "delete",
    "detach", "drop", "explain", "insert", "pragma", "reindex", "release",
    "returning", "rollback", "savepoint", "update", "vacuum",
    "load_extension", "readfile", "writefile", "fts3_tokenizer",
}
# Palabras reservadas de SQLite que cierran la lista FROM. No se incluyen las
# que SQLite deja usar como alias (WINDOW, FILTER...): si cerrasen la lista,
# se colaría `FROM t window, otra_tabla`.
_SQL_FROM_END = {"where", "group", "having", "order", "limit", "union", "intersect", "except"}
# Nombres alternativos del catálogo: se comparan por su nombre canónico.
_SQL_ALIASES = {"sqlite_schema": "sqlite_master", "sqlite_temp_schema": "sqlite_temp_master"}
_ASCII_LOWER =str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

Token = tuple[str, str]


def sql_fold(name: str) -> str:
    """Minúsculas solo ASCII, que es como compara SQLite los identificadores."""
    return name.translate(_ASCII_LOWER)


def _is_ident_char(c: str) -> bool:
    return (c.isascii() and (c.isalnum() or c in "_$")) or ord(c) >= 0x80


def sql_tokens(sql: str) -> list[Token]:
    """Trocea SQL siguiendo las reglas léxicas de SQLite.

    Devuelve (tipo, valor) con tipo 'word' (identificador o palabra clave sin
    comillas, en minúsculas ASCII), 'name' (identificador entre comillas
    dobles, acentos graves o corchetes), 'str' (literal de texto, que SQLite
    también acepta como nombre de tabla), 'num', 'blob' u 'op'. Los
    comentarios desaparecen. Cualquier cosa rara -> Denied.
    """
    out: list[Token] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c in _SQL_WS:
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise Denied("Consulta no válida: comentario sin cerrar.")
            i = j + 2
        elif c in "'\"`":
            j, buf = i + 1, []
            while True:
                k = sql.find(c, j)
                if k < 0:
                    raise Denied("Consulta no válida: comillas sin cerrar.")
                buf.append(sql[j:k])
                if sql.startswith(c * 2, k):  # comilla escapada duplicándola
                    buf.append(c)
                    j = k + 2
                    continue
                i = k + 1
                break
            out.append(("str" if c == "'" else "name", "".join(buf)))
        elif c == "[":
            k = sql.find("]", i + 1)
            if k < 0:
                raise Denied("Consulta no válida: corchete sin cerrar.")
            out.append(("name", sql[i + 1 : k]))
            i = k + 1
        elif c in "xX" and sql.startswith("'", i + 1):
            k = sql.find("'", i + 2)
            if k < 0:
                raise Denied("Consulta no válida: comillas sin cerrar.")
            out.append(("blob", sql[i + 2 : k]))
            i = k + 1
        elif c.isascii() and c.isdigit() or (c == "." and sql[i + 1 : i + 2].isdigit()):
            j = i + 1
            while j < n and sql[j].isascii() and (sql[j].isalnum() or sql[j] in "._"):
                j += 1
            out.append(("num", sql[i:j]))
            i = j
        elif c != "$" and _is_ident_char(c):
            j = i + 1
            while j < n and _is_ident_char(sql[j]):
                j += 1
            out.append(("word", sql_fold(sql[i:j])))
            i = j
        elif c in _SQL_PUNCT:
            out.append(("op", c))
            i += 1
        else:  # parámetros (?, :x, @x, $x) y cualquier otro carácter
            raise Denied("Consulta no válida: carácter no admitido.")
    return out


def _skip_parens(toks: list[Token], i: int) -> int:
    if toks[i : i + 1] != [("op", "(")]:
        raise Denied("Consulta no válida: WITH mal formado.")
    depth = 0
    for j in range(i, len(toks)):
        if toks[j] == ("op", "("):
            depth += 1
        elif toks[j] == ("op", ")"):
            depth -= 1
            if depth == 0:
                return j + 1
    raise Denied("Consulta no válida: paréntesis sin cerrar.")


def _cte_names(toks: list[Token]) -> set[str]:
    """Nombres de las CTE del WITH inicial (su ámbito es toda la sentencia)."""
    names: set[str] = set()
    if toks[:1] != [("word", "with")]:
        return names
    i = 1
    if toks[i : i + 1] == [("word", "recursive")]:
        i += 1
    while True:
        if i >= len(toks) or toks[i][0] not in ("word", "name", "str"):
            raise Denied("Consulta no válida: WITH mal formado.")
        names.add(sql_fold(toks[i][1]))
        i += 1
        if toks[i : i + 1] == [("op", "(")]:  # lista de columnas
            i = _skip_parens(toks, i)
        if toks[i : i + 1] != [("word", "as")]:
            raise Denied("Consulta no válida: WITH mal formado.")
        i += 1
        if toks[i : i + 1] == [("word", "not")]:
            i += 1
        if toks[i : i + 1] == [("word", "materialized")]:
            i += 1
        i = _skip_parens(toks, i)
        if toks[i : i + 1] != [("op", ",")]:
            return names
        i += 1


def sql_tables(sql: Any) -> set[str]:
    """Tablas que lee una consulta de SQLite (nombres en minúsculas ASCII).

    Lanza Denied si no es un único SELECT (o WITH ... SELECT, o VALUES) que se
    pueda analizar con seguridad: varias sentencias, escritura, ATTACH,
    PRAGMA, WITH anidados, nombres con esquema (`main.t`), funciones de tabla
    (`json_each(...)`, `pragma_table_info(...)`), parámetros...

    Las CTE del WITH inicial no se devuelven: su cuerpo se analiza entero y,
    al ser de toda la sentencia, una CTE oculta a la tabla homónima.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise Denied("Consulta no válida.")
    toks = sql_tokens(sql)
    while toks and toks[-1] == ("op", ";"):
        toks.pop()
    if not toks:
        raise Denied("Consulta no válida.")
    if ("op", ";") in toks:
        raise Denied("Solo se admite una sentencia por consulta.")
    if toks[0] not in (("word", "select"), ("word", "with"), ("word", "values")):
        raise Denied("Solo se admiten consultas SELECT.")
    for idx, (kind, value) in enumerate(toks):
        if kind != "word":
            continue
        if value in _SQL_FORBIDDEN_WORDS:
            raise Denied("Solo se admiten consultas SELECT.")
        if value == "replace" and toks[idx + 1 : idx + 2] != [("op", "(")]:
            raise Denied("Solo se admiten consultas SELECT.")  # REPLACE INTO
        if value == "with" and idx != 0:
            raise Denied("Consulta no admitida: WITH solo al principio.")

    ctes = _cte_names(toks)
    tables: set[str] = set()

    def table_at(i: int) -> int:
        """Lee un nombre de tabla en toks[i] y devuelve el índice siguiente."""
        if i >= len(toks) or toks[i][0] not in ("word", "name", "str"):
            raise Denied("Consulta no admitida: no se reconoce la tabla.")
        nxt = toks[i + 1 : i + 2]
        if nxt == [("op", ".")]:
            raise Denied("Consulta no admitida: no se permiten nombres con esquema.")
        if nxt == [("op", "(")]:
            raise Denied("Consulta no admitida: no se permiten funciones de tabla.")
        name = sql_fold(toks[i][1])
        name = _SQL_ALIASES.get(name, name)
        if name not in ctes:
            tables.add(name)
        return i + 1

    # Un marco por nivel de paréntesis: [en lista FROM, toca nombre de tabla].
    # Se sobreestiman las posiciones de tabla: si algo que no es tabla cae en
    # una, se deniega (fallo seguro); lo grave sería saltarse una de verdad.
    stack: list[list[bool]] = [[False, False]]
    i = 0
    while i < len(toks):
        kind, value = toks[i]
        frame = stack[-1]
        if frame[1]:
            frame[1] = False
            if (kind, value) == ("op", "("):
                # Subconsulta, o joins entre paréntesis: `FROM (a JOIN b)`.
                sub = toks[i + 1 : i + 2] in ([("word", "select")], [("word", "values")])
                stack.append([not sub, not sub])
                i += 1
                continue
            i = table_at(i)
            continue
        if kind == "op":
            if value == "(":
                stack.append([False, False])
            elif value == ")":
                stack.pop()
                if not stack:
                    raise Denied("Consulta no válida: paréntesis desparejados.")
            elif value == "," and frame[0]:
                frame[1] = True
        elif kind == "word":
            if value == "from":
                frame[0] = frame[1] = True
            elif value == "join":
                if not frame[0]:
                    raise Denied("Consulta no admitida: JOIN fuera de FROM.")
                frame[1] = True
            elif value in _SQL_FROM_END:
                frame[0] = False
            elif value == "in" and toks[i + 1 : i + 2] != [("op", "(")]:
                i = table_at(i + 1)  # `x IN tabla` es SQL válido en SQLite
                continue
        i += 1
    if len(stack) != 1:
        raise Denied("Consulta no válida: paréntesis desparejados.")
    if stack[0][1]:
        raise Denied("Consulta no válida: falta la tabla.")
    return tables


def check_tables_rule(rule: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Regla `tables`: la consulta solo puede leer tablas de la lista."""
    sql = arguments.get(rule.get("argument", "sql_query"))
    allowed = {sql_fold(t) for t in rule["tables"]}
    if sql_tables(sql) - allowed:
        listed = ", ".join(rule["tables"]) or "(ninguna)"
        raise Denied(f"Consulta no permitida: solo puedes leer las tablas {listed}.")


def apply_rules(contact: str, tool: str, arguments: dict[str, Any], rules: dict[str, Any]) -> None:
    """Comprueba las reglas por argumento de `contact` para `tool`.

    Sin regla para ese contacto y tool no hace nada (manda la lista blanca).
    Con regla, cerrado por defecto: lo que no se pueda comprobar se deniega.
    """
    rule = rules.get(contact, {}).get(tool)
    if rule is None:
        return
    if not isinstance(arguments, dict) or not isinstance(rule, dict):
        raise Denied("Tool no disponible.")
    if "tables" in rule:
        check_tables_rule(rule, arguments)
    else:  # `Rules` ya lo valida al cargar; esto es por si acaso
        raise Denied("Tool no disponible.")


def apply_policy(
    contact: str,
    tool: str,
    arguments: dict[str, Any],
    rules: dict[str, Any],
    path_args: dict[str, str],
    path_root: str | None,
) -> dict[str, Any]:
    """Toda la política de argumentos: reglas del contacto y después rutas."""
    apply_rules(contact, tool, arguments, rules)
    return apply_path_policy(tool, arguments, path_args, path_root)
