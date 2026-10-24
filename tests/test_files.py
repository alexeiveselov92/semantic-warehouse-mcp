"""A team's YAML files read as written, and every problem with one named — never with a line of the file, which may
hold a secret, and never as a traceback."""

import pathlib

import pytest

from semantic_warehouse_mcp import files
from semantic_warehouse_mcp.config import ConfigError, load


def read(tmp_path: pathlib.Path, text: str | bytes):
    path = tmp_path / "file.yaml"
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return files.load_file(path)


def test_yaml_is_read_as_written(tmp_path):
    """`no`, `on` and dates stay the text written (YAML 1.1 would make them false, true and a date); only `true` and
    `false` are booleans."""
    data = read(tmp_path, "country: NO\non: daily\nat: 2026-03-01 10:00:00\nday: 2026-03-01\nyes: [true, False]\n")
    assert data == {"country": "NO", "on": "daily", "at": "2026-03-01 10:00:00", "day": "2026-03-01",
                    "yes": [True, False]}  # fmt: skip


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a: 1\na: 2\n", "not valid YAML: the key 'a' appears twice in one mapping (line 2, column 1)"),
        ("? [a, b]\n: c\n", "not valid YAML: a key must be a plain value, not a list or a mapping (line 1, column 3)"),
        ("n: " + "9" * 5000 + "\n", "not valid YAML: a value that cannot be read: Exceeds the limit"),
        ("a: [1, 2\n", "not valid YAML: while parsing a flow sequence expected ',' or ']'"),
        ("a: &x [*x]\n", "not valid YAML: an alias refers to the mapping or list that holds it"),
        ("[" * 2000 + "]" * 2000, "not valid YAML: "),
        ("café: x\n".encode("latin-1"), "not UTF-8 text (byte 3 is not)"),
    ],
)
def test_a_file_that_cannot_be_read_is_named(tmp_path, text, expected):
    with pytest.raises(files.FileProblem) as problem:
        read(tmp_path, text)
    assert str(problem.value).startswith(expected), problem.value


def test_aliases_may_not_expand_past_a_million_values(tmp_path):
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    lines += [f"a{n}: &a{n} [{', '.join([f'*a{n - 1}'] * 10)}]" for n in range(1, 7)]  # 10^7 values in 7 lines
    with pytest.raises(files.FileProblem, match="its aliases expand to more than 1,000,000 values"):
        read(tmp_path, "\n".join(lines) + "\n")
    assert len(read(tmp_path, "\n".join(lines[:5]) + "\n")) == 5  # 10^5: shared, read as written


def test_no_problem_repeats_a_password_written_in_a_url(tmp_path):
    body = (
        "connections:\n"
        "  - name: shop\n    engine: clickhouse\n    url: https://bot:hunter2@ch.example.com\n"
        "    url: https://bot:hunter2@ch.example.com\n    readable: ['marts.*']\n"
    )
    (tmp_path / "config.yaml").write_text(body)
    with pytest.raises(ConfigError) as error:
        load(tmp_path / "config.yaml")
    assert "hunter2" not in str(error.value)
    assert error.value.problems == ["not valid YAML: the key 'url' appears twice in one mapping (line 5, column 5)"]
    assert files.redact("see https://bot:hunter2@ch.example.com/x") == "see https://…@ch.example.com/x"


@pytest.mark.parametrize("password", ["hun@ter2", "hun/ter2", "h@n/t:er2"])
def test_a_password_holding_at_or_slash_is_left_out_whole(password):
    assert files.redact(f"see https://bot:{password}@ch.example.com/x") == "see https://…@ch.example.com/x"


def test_a_mapping_holding_a_secret_is_never_repeated(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "connections:\n  - name: shop\n    engine: duckdb\n    path: x.duckdb\n    readable: ['marts.*']\n"
        "    description: {token: hunter2}\ninstructions: [{nested: {password: hunter2}}]\ncontext: []\n"
    )
    with pytest.raises(ConfigError) as error:
        load(tmp_path / "config.yaml")
    assert "hunter2" not in str(error.value)
    assert 'connections[0] "shop" → description: input should be a valid string (got a mapping)' in error.value.problems
    (tmp_path / "config.yaml").write_text("connections: []\n")
    with pytest.raises(ConfigError, match=r"\(got \[\]\)"):  # nothing in it can be a secret: shown as it is
        load(tmp_path / "config.yaml")


@pytest.mark.parametrize("text", ['a: "x\\ud800y"\n', 'a: {"\\udfff": 1}\n', '["\\ud83d"]'])
def test_text_that_is_not_unicode_is_refused(tmp_path, text):
    """A lone surrogate, escaped, reads as text no answer can carry: refused, not served to break the server."""
    with pytest.raises(files.FileProblem, match=r"text that is not Unicode — a lone surrogate, \\ud"):
        read(tmp_path, text)
    assert read(tmp_path, 'a: "\\ud83d\\ude00"\n') == {"a": "😀"}  # a pair is a character
