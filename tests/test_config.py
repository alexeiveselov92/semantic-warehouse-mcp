"""The configuration's check: a wrong entry stops the server, naming it (docs/reference/configuration.md)."""

import pathlib
import shutil
import subprocess
import sys

import pytest
from conftest import write_config

from semantic_warehouse_mcp import adapters
from semantic_warehouse_mcp.adapters import EngineSettings
from semantic_warehouse_mcp.adapters.duckdb import DuckDBAdapter
from semantic_warehouse_mcp.config import ConfigError, load, parse
from semantic_warehouse_mcp.server import main

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"

GOOD = """
connections:
  - name: shop
    engine: duckdb
    path: shop.duckdb
    readable: ["marts.*"]
"""


def problems(tmp_path, body: str) -> list[str]:
    with pytest.raises(ConfigError) as error:
        load(write_config(tmp_path, body))
    return error.value.problems


def test_a_good_configuration_and_its_defaults(shop_path):
    config = load(write_config(shop_path.parent, GOOD))
    shop = config.connections["shop"]
    assert shop.limits.rows == 500
    assert shop.limits.seconds == 30
    assert shop.large_tables == ()
    assert config.limits.answer_characters == 40_000
    assert shop.adapter.catalog == "shop"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("[1, 2]", "the file must hold a mapping with `connections:` at the top"),
        ("limits: {}", "connections: required"),
        ("connections: []", "connections: list should have at least 1 item after validation, not 0 (got [])"),
        (GOOD + "conections: []", "conections: unknown key — did you mean 'connections'?"),
        (
            GOOD + "limits: {answer_chars: 5000}",
            "limits → answer_chars: unknown key — did you mean 'answer_characters'?",
        ),
        (GOOD + "limits: {answer_characters: 10}", "limits → answer_characters: input should be greater than or equal"),
        (GOOD.replace("name: shop", "name: Shop DB"), 'connections[0] "Shop DB" → name: string should match pattern'),
        (
            GOOD.replace("engine: duckdb", "engine: oracle"),
            "engine: 'oracle' is not supported (supported: clickhouse, duckdb)",
        ),
        (GOOD.replace("    engine: duckdb\n", ""), 'connections[0] "shop" → engine: required'),
        (GOOD.replace("    path: shop.duckdb\n", ""), 'connections[0] "shop" → path: required'),
        (GOOD.replace("path:", "paht:"), "connections[0] \"shop\" → paht: unknown key — did you mean 'path'?"),
        (GOOD.replace('readable: ["marts.*"]', "readable: []"), "readable: list should have at least 1 item"),
        (GOOD.replace("marts.*", "marts"), "readable: 'marts' must be schema.table (globs allowed: marts.*, *.*)"),
        (GOOD.replace("marts.*", "a.b.c"), "readable: 'a.b.c' must be schema.table"),
        (GOOD.replace('readable: ["marts.*"]', "readable: marts.*"), "readable: input should be a valid list"),
        (
            GOOD + "    large_tables: [{table: raw.events, bound_by: event_date}]",
            "large_tables: 'raw.events' is not covered by any readable pattern",
        ),
        (GOOD + "    large_tables: [{table: marts.events}]", "large_tables[0] → bound_by: required"),
        (
            GOOD + "    limits: {rows: 0}",
            'connections[0] "shop" → limits → rows: input should be greater than 0 (got 0)',
        ),
        (GOOD + "    limits: {rows: '10'}", "limits → rows: input should be a valid integer (got '10')"),
        (GOOD + "    limits: {row: 10}", "limits → row: unknown key — did you mean 'rows'?"),
        (
            GOOD + "  - name: shop\n    engine: duckdb\n    path: x\n    readable: [a.b]",
            "another connection has this name",
        ),
        (GOOD.replace("shop.duckdb", "missing.duckdb"), "missing.duckdb does not exist"),
    ],
)
def test_a_wrong_entry_is_named(shop_path, body, expected):
    found = problems(shop_path.parent, body)
    assert any(expected in problem for problem in found), found


def test_every_problem_is_named_at_once(shop_path):
    body = GOOD.replace("readable:", "readble:").replace("path:", "paht:")
    found = problems(shop_path.parent, body)
    assert any("readble: unknown key — did you mean 'readable'?" in p for p in found), found
    assert any("paht: unknown key — did you mean 'path'?" in p for p in found), found
    assert any("readable: required" in p for p in found), found


def test_a_key_twice_is_named_with_its_line(tmp_path):
    found = problems(tmp_path, GOOD + '    readable: ["raw.*"]\n')
    assert len(found) == 1
    assert "the key 'readable' appears twice" in found[0]
    assert "line 7" in found[0]


def test_unreadable_files(tmp_path):
    with pytest.raises(ConfigError, match="cannot read the file"):
        load(tmp_path / "absent.yaml")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load(write_config(tmp_path, "connections: [\n"))


class SecretSettings(EngineSettings):
    password_env: str


class SecretAdapter(DuckDBAdapter):
    engine = "secretive"
    settings_model = SecretSettings


def test_a_secret_written_by_mistake_is_never_repeated(monkeypatch, tmp_path):
    monkeypatch.setitem(adapters.ENGINES, "secretive", f"{__name__}:SecretAdapter")
    data = {"connections": [{"name": "x", "engine": "secretive", "readable": ["a.b"], "password_env": 271828}]}
    with pytest.raises(ConfigError) as error:
        parse(data, source="test", base_dir=tmp_path, reach=False)
    assert "password_env: input should be a valid string" in str(error.value)
    assert "271828" not in str(error.value)


def test_the_command_stops_naming_the_problem(tmp_path, capsys):
    path = write_config(tmp_path, GOOD.replace("path:", "paht:"))
    with pytest.raises(SystemExit) as stop:
        main(["--config", str(path)])
    assert stop.value.code == 2
    assert "paht: unknown key — did you mean 'path'?" in capsys.readouterr().err


def test_the_command_checks_and_exits(shop_path, capsys):
    main(["--config", str(write_config(shop_path.parent, GOOD)), "--check"])
    assert "shop (duckdb): reached; readable marts.*" in capsys.readouterr().out


def test_the_example_configuration_holds(tmp_path):
    """examples/config.example.yaml, over the made-up shop its script builds."""
    shutil.copy(EXAMPLES / "config.example.yaml", tmp_path / "config.example.yaml")
    shutil.copytree(EXAMPLES / "context", tmp_path / "context")
    (tmp_path / "data").mkdir()
    subprocess.run(
        [sys.executable, str(EXAMPLES / "data" / "make_shop.py"), str(tmp_path / "data" / "shop.duckdb")], check=True
    )
    config = load(tmp_path / "config.example.yaml")
    assert set(config.connections) == {"shop"}
    assert config.connections["shop"].context == ((tmp_path / "context").resolve(),)
