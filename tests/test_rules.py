import json
import os
import sqlite3

import pytest

from beira.config import Config, Rules, parse_rules
from beira.policy import Denied, apply_policy, apply_rules, sql_tables

RULES = {"pau": {"query_personal_db": {"tables": ["garmin_daily"]}}}
TOOL = "query_personal_db"


def check(sql, rules=RULES, contact="pau"):
    apply_rules(contact, TOOL, {"sql_query": sql}, rules)


# Una base real con el mismo esquema, para comprobar contra SQLite de verdad.
@pytest.fixture(scope="module")
def db():
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE garmin_daily (day TEXT, steps INTEGER, hr INTEGER);
        CREATE TABLE hevy_sets (day TEXT, exercise TEXT, kg REAL);
        CREATE TABLE cardio_activities (day TEXT, km REAL);
        INSERT INTO garmin_daily VALUES ('2026-01-01', 1000, 60);
        INSERT INTO hevy_sets VALUES ('2026-01-01', 'squat', 100);
        """
    )
    return con


def real_reads(con, sql) -> set[str]:
    """Tablas que SQLite lee de verdad al preparar `sql` (vía su authorizer)."""
    seen: set[str] = set()

    def auth(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_READ and arg1:
            seen.add(arg1.lower())
        return sqlite3.SQLITE_OK

    con.set_authorizer(auth)
    try:
        con.execute(sql).fetchall()
    finally:
        con.set_authorizer(None)
    return seen


ALLOWED = [
    "SELECT * FROM garmin_daily",
    "select day, steps from garmin_daily where steps > 500 order by day limit 5",
    "SELECT * FROM GARMIN_DAILY",
    'SELECT * FROM "garmin_daily"',
    "SELECT * FROM [garmin_daily]",
    "SELECT * FROM `garmin_daily`",
    "SELECT * FROM 'garmin_daily'",
    "SELECT g.day FROM garmin_daily AS g JOIN garmin_daily h ON g.day = h.day",
    "SELECT * FROM garmin_daily, garmin_daily AS b",
    "SELECT * FROM (SELECT * FROM garmin_daily) AS t",
    "SELECT (SELECT max(steps) FROM garmin_daily), count(*) FROM garmin_daily",
    "WITH s AS (SELECT * FROM garmin_daily) SELECT * FROM s",
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 3) SELECT * FROM c",
    "SELECT * FROM garmin_daily WHERE day IN (SELECT day FROM garmin_daily)",
    "SELECT * FROM garmin_daily WHERE day IN ('a', 'b')",
    "SELECT 'hevy_sets', 'FROM hevy_sets' FROM garmin_daily -- hevy_sets",
    "SELECT replace(day, '-', '') FROM garmin_daily;",
    "SELECT 1",
    "SELECT * FROM garmin_daily /* JOIN hevy_sets */ WHERE 1",
    "SELECT steps FROM garmin_daily UNION SELECT steps FROM garmin_daily",
    "SELECT * FROM (garmin_daily)",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_queries(sql, db):
    check(sql)
    # Y SQLite confirma que solo lee tablas permitidas.
    assert real_reads(db, sql) <= {"garmin_daily"}


FORBIDDEN = [
    # Directo y variantes de escritura del nombre.
    "SELECT * FROM hevy_sets",
    "SELECT * FROM HEVY_SETS",
    "select * from Hevy_Sets",
    'SELECT * FROM "hevy_sets"',
    "SELECT * FROM [hevy_sets]",
    "SELECT * FROM `hevy_sets`",
    "SELECT * FROM 'hevy_sets'",
    'SELECT * FROM "hevy""sets"',
    # Comentarios y espacios para despistar.
    "SELECT * FROM/**/hevy_sets",
    "SELECT * FROM garmin_daily--\n, hevy_sets",
    "SELECT * FROM\n\thevy_sets",
    "SELECT * FROM garmin_daily /* */ JOIN /* */ hevy_sets ON 1",
    # Joins y comas.
    "SELECT * FROM garmin_daily JOIN hevy_sets",
    "SELECT * FROM garmin_daily NATURAL LEFT OUTER JOIN hevy_sets",
    "SELECT * FROM garmin_daily CROSS JOIN cardio_activities",
    "SELECT * FROM garmin_daily, hevy_sets",
    "SELECT * FROM garmin_daily g, hevy_sets h",
    "SELECT * FROM garmin_daily JOIN garmin_daily b ON 1, hevy_sets",
    "SELECT * FROM garmin_daily AS window, hevy_sets",
    "SELECT * FROM (garmin_daily JOIN hevy_sets)",
    "SELECT * FROM (garmin_daily, (hevy_sets))",
    # Subconsultas en todas partes.
    "SELECT * FROM (SELECT * FROM hevy_sets)",
    "SELECT (SELECT kg FROM hevy_sets) FROM garmin_daily",
    "SELECT * FROM garmin_daily WHERE day IN (SELECT day FROM hevy_sets)",
    "SELECT * FROM garmin_daily WHERE EXISTS (SELECT 1 FROM hevy_sets)",
    "SELECT * FROM garmin_daily WHERE day IN hevy_sets",
    "SELECT * FROM garmin_daily WHERE day NOT IN hevy_sets",
    "SELECT * FROM garmin_daily g JOIN garmin_daily h ON h.day = (SELECT day FROM hevy_sets)",
    "SELECT * FROM garmin_daily ORDER BY (SELECT kg FROM hevy_sets)",
    "SELECT * FROM garmin_daily UNION SELECT * FROM hevy_sets",
    "SELECT day FROM garmin_daily EXCEPT SELECT day FROM cardio_activities",
    # CTE.
    "WITH s AS (SELECT * FROM hevy_sets) SELECT * FROM s",
    "WITH garmin_daily AS (SELECT * FROM hevy_sets) SELECT * FROM garmin_daily",
    "WITH s AS (SELECT 1) SELECT * FROM s, hevy_sets",
    "SELECT * FROM (WITH hevy_sets AS (SELECT 1) SELECT * FROM hevy_sets), hevy_sets",
    # Catálogo y esquema.
    "SELECT * FROM sqlite_master",
    "SELECT * FROM sqlite_schema",
    "SELECT * FROM SQLITE_MASTER",
    "SELECT * FROM sqlite_temp_master",
    "SELECT * FROM main.garmin_daily",
    "SELECT * FROM main.hevy_sets",
    'SELECT * FROM "main"."hevy_sets"',
    "SELECT * FROM pragma_table_info('hevy_sets')",
    "SELECT * FROM json_each('[1]')",
    "SELECT * FROM garmin_daily WHERE day IN json_each('[1]')",
    "PRAGMA table_info(hevy_sets)",
    # Otras sentencias.
    "ATTACH DATABASE 'personal_data.db' AS x",
    "SELECT 1; ATTACH DATABASE 'x.db' AS x",
    "SELECT * FROM garmin_daily; SELECT * FROM hevy_sets",
    "DELETE FROM garmin_daily",
    "WITH s AS (SELECT 1) DELETE FROM garmin_daily",
    "WITH s AS (SELECT 1) REPLACE INTO garmin_daily VALUES (1, 2, 3)",
    "INSERT INTO garmin_daily SELECT * FROM garmin_daily",
    "UPDATE garmin_daily SET steps = 0",
    "DROP TABLE garmin_daily",
    "CREATE TABLE t AS SELECT * FROM garmin_daily",
    "EXPLAIN SELECT * FROM hevy_sets",
    "SELECT load_extension('x')",
    "SELECT * FROM garmin_daily RETURNING *",
    # Lo que no se entiende, se deniega.
    "SELECT * FROM garmin_daily WHERE day = ?",
    "SELECT * FROM garmin_daily WHERE day = :d",
    "SELECT * FROM garmin_daily WHERE day = $d",
    "SELECT * FROM garmin_daily /* sin cerrar",
    "SELECT * FROM 'garmin_daily",
    "SELECT * FROM (SELECT * FROM garmin_daily",
    "SELECT * FROM garmin_daily)",
    "SELECT * FROM",
    "SELECT * FROM garmin_daily\x00",
    "",
    "   ",
    "-- solo un comentario",
    ";",
]


@pytest.mark.parametrize("sql", FORBIDDEN)
def test_forbidden_queries(sql):
    with pytest.raises(Denied):
        check(sql)


@pytest.mark.parametrize("args", [{}, {"sql_query": None}, {"sql_query": 42}, {"sql_query": ["SELECT 1"]}, {"otro": "SELECT 1"}])
def test_missing_or_odd_argument_is_denied(args):
    with pytest.raises(Denied):
        apply_rules("pau", TOOL, args, RULES)


def test_soundness_against_sqlite(db):
    """Para toda consulta que el análisis acepta, SQLite no lee más de lo dicho."""
    for sql in ALLOWED + FORBIDDEN:
        try:
            tables = sql_tables(sql)
        except Denied:
            continue
        try:
            reads = real_reads(db, sql)
        except sqlite3.Error:
            continue
        assert reads <= tables, sql


def test_without_rule_nothing_changes():
    check("SELECT * FROM hevy_sets", contact="marta")
    apply_rules("pau", "otra_tool", {"sql_query": "SELECT * FROM hevy_sets"}, RULES)
    apply_rules("pau", TOOL, {"sql_query": "DROP TABLE x"}, {})


def test_several_tables_and_case_insensitive_rule():
    rules = {"pau": {TOOL: {"tables": ["Garmin_Daily", "hevy_sets"]}}}
    check("SELECT * FROM garmin_daily JOIN HEVY_SETS", rules)
    with pytest.raises(Denied):
        check("SELECT * FROM garmin_daily JOIN cardio_activities", rules)


def test_empty_table_list_denies_every_table():
    rules = {"pau": {TOOL: {"tables": []}}}
    check("SELECT 1", rules)
    with pytest.raises(Denied):
        check("SELECT * FROM garmin_daily", rules)


def test_custom_argument_name():
    rules = {"pau": {"otra_db": {"tables": ["a"], "argument": "q"}}}
    apply_rules("pau", "otra_db", {"q": "SELECT * FROM a"}, rules)
    with pytest.raises(Denied):
        apply_rules("pau", "otra_db", {"q": "SELECT * FROM b"}, rules)


def test_apply_policy_chains_rules_and_paths():
    path_args = {"read_obsidian_note": "note_path"}
    assert apply_policy("pau", TOOL, {"sql_query": "SELECT 1"}, RULES, path_args, None) == {"sql_query": "SELECT 1"}
    with pytest.raises(Denied):
        apply_policy("pau", TOOL, {"sql_query": "SELECT * FROM hevy_sets"}, RULES, path_args, None)
    out = apply_policy("pau", "read_obsidian_note", {"note_path": "a.md"}, RULES, path_args, "Compartido/pau")
    assert out == {"note_path": "Compartido/pau/a.md"}
    with pytest.raises(Denied):
        apply_policy("pau", "read_obsidian_note", {"note_path": "../a.md"}, RULES, path_args, "Compartido/pau")


# --- carga de BEIRA_RULES ------------------------------------------------------

@pytest.mark.parametrize(
    "data",
    [
        [],
        {"pau": []},
        {"pau": {TOOL: []}},
        {"pau": {TOOL: {}}},
        {"pau": {TOOL: {"tables": "garmin_daily"}}},
        {"pau": {TOOL: {"tables": [1]}}},
        {"pau": {TOOL: {"tables": [""]}}},
        {"pau": {TOOL: {"tables": ["a"], "argument": 1}}},
        {"pau": {TOOL: {"tables": ["a"], "tablas": ["b"]}}},
    ],
)
def test_parse_rules_rejects_malformed(data):
    with pytest.raises(ValueError):
        parse_rules(data)


def test_rules_file_missing_means_no_rules(tmp_path):
    assert Rules(str(tmp_path / "nope.json")).get() == {}


def test_rules_file_default_location_and_env(tmp_path):
    (tmp_path / "rules.json").write_text(json.dumps(RULES), encoding="utf-8")
    assert Config(db_path=str(tmp_path / "beira.db")).rules.get() == RULES
    other = tmp_path / "otras.json"
    other.write_text("{}", encoding="utf-8")
    cfg = Config.from_env({"BEIRA_DB": str(tmp_path / "beira.db"), "BEIRA_RULES": str(other)})
    assert cfg.rules.path == str(other) and cfg.rules.get() == {}


def test_malformed_rules_file_fails_at_start(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{ no es json", encoding="utf-8")
    with pytest.raises(ValueError):
        Rules(str(path))


def test_rules_reload_and_fail_closed(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{}", encoding="utf-8")
    rules = Rules(str(path))
    assert rules.get() == {}

    path.write_text(json.dumps(RULES), encoding="utf-8")
    os.utime(path, ns=(1, 1))  # fuerza un mtime distinto aunque el reloj sea grueso
    assert rules.get() == RULES

    path.write_text('{"pau": {"query_personal_db": {"tablas": []}}}', encoding="utf-8")
    os.utime(path, ns=(2, 2))
    with pytest.raises(Denied):
        rules.get()

    path.unlink()
    assert rules.get() == {}
