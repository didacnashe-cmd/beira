import pytest

from beira.policy import Denied, apply_path_policy, confine, normalize_root

ROOT = "Compartido/pau"
PATH_ARGS = {"read_obsidian_note": "note_path", "list_obsidian_notes": "subfolder"}


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", ROOT),
        (None, ROOT),
        ("a.md", f"{ROOT}/a.md"),
        ("sub/a.md", f"{ROOT}/sub/a.md"),
        ("./sub//a.md", f"{ROOT}/sub/a.md"),
        ("sub\\a.md", f"{ROOT}/sub/a.md"),
        # Las rutas que devuelve el listado ya traen el prefijo: no se duplica.
        (f"{ROOT}/a.md", f"{ROOT}/a.md"),
        (ROOT, ROOT),
        # Otra carpeta con nombre parecido queda DENTRO de la del contacto.
        ("Compartido/pau2/a.md", f"{ROOT}/Compartido/pau2/a.md"),
        ("Compartido/marta/a.md", f"{ROOT}/Compartido/marta/a.md"),
        ("Privado/diario.md", f"{ROOT}/Privado/diario.md"),
    ],
)
def test_confine_stays_inside_root(value, expected):
    assert confine(value, ROOT) == expected


@pytest.mark.parametrize(
    "value",
    [
        "../secreto.md",
        "sub/../../secreto.md",
        "..",
        "..\\..\\secreto.md",
        f"{ROOT}/../marta/a.md",
        ".. /a.md",
        "/etc/passwd",
        "\\\\servidor\\recurso",
        "C:\\Windows\\win.ini",
        "c:secreto.md",
        "~/.ssh/id_ed25519",
        "a\x00.md",
        42,
        ["a.md"],
    ],
)
def test_confine_rejects_escapes(value):
    with pytest.raises(Denied):
        confine(value, ROOT)


def test_normalize_root():
    assert normalize_root("Compartido/pau/") == "Compartido/pau"
    for bad in ["", "/", "../x", "/abs", "a/../b"]:
        with pytest.raises(Denied):
            normalize_root(bad)


def test_policy_rewrites_only_the_path_argument():
    out = apply_path_policy("read_obsidian_note", {"note_path": "a.md", "x": 1}, PATH_ARGS, ROOT)
    assert out == {"note_path": f"{ROOT}/a.md", "x": 1}


def test_policy_injects_root_when_argument_is_missing():
    assert apply_path_policy("list_obsidian_notes", {}, PATH_ARGS, ROOT) == {"subfolder": ROOT}


def test_policy_denies_path_tool_without_root():
    with pytest.raises(Denied):
        apply_path_policy("read_obsidian_note", {"note_path": "a.md"}, PATH_ARGS, None)


def test_policy_leaves_other_tools_untouched():
    args = {"sql_query": "select 1"}
    assert apply_path_policy("query_personal_db", args, PATH_ARGS, ROOT) == args
