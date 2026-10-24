"""Skills: folders holding a SKILL.md, checked by the format's rules at start, served as written with their files."""

import json

import pytest
from conftest import write_config
from mcp import Client

from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

REVENUE = """---
name: revenue-questions
description: How to answer a question about revenue — which metric, which grouping, what to check.
license: Apache-2.0
metadata:
  author: acme
---

1. Use the `revenue` metric. The rules of grouping are in [rules](references/grouping.md).
2. Run `scripts/check.sql` to see the totals agree.
"""
GROUPING = "# Grouping\n\nGroup by currency unless the question names one.\n"
CHECK = "select currency, sum(amount) from marts.orders group by currency\n"
REPORT = "---\nname: weekly-report\ndescription: How the weekly report is put together.\n---\n\nSee the dashboard.\n"

CONFIG = """
skills: [team-skills]
connections:
  - name: shop
    engine: duckdb
    path: shop.duckdb
    readable: ["marts.*"]
    skills: [shop-skills/revenue-questions]
limits:
  answer_characters: {answer_characters}
"""


@pytest.fixture
def team(shop_path):
    folder = shop_path.parent
    revenue = folder / "shop-skills" / "revenue-questions"
    (revenue / "references").mkdir(parents=True)
    (revenue / "scripts").mkdir()
    (revenue / ".git").mkdir()
    (revenue / "SKILL.md").write_text(REVENUE)
    (revenue / "references" / "grouping.md").write_text(GROUPING)
    (revenue / "references" / "long.txt").write_text("A line of the long reference.\n" * 300)
    (revenue / "scripts" / "check.sql").write_text(CHECK)
    (revenue / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    (revenue / ".git" / "config").write_text("not served")
    (revenue / ".notes.md").write_text("not served")
    report = folder / "team-skills" / "weekly-report"
    report.mkdir(parents=True)
    (report / "SKILL.md").write_text(REPORT)
    (folder / "team-skills" / "drafts").mkdir()  # a folder without a SKILL.md: not a skill, left alone
    (folder / "team-skills" / "README.md").write_text("The team's skills.")
    (folder / "outside.md").write_text("not in any skill")

    def build(answer_characters=40_000, body=None):
        return load(write_config(folder, (body or CONFIG).format(answer_characters=answer_characters)))

    return build


async def call(config, tool, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


async def test_skills_are_listed_with_their_descriptions_as_written(team):
    answer, _ = await call(team(), "list_skills")
    assert answer["skills"] == [
        {"name": "weekly-report", "description": "How the weekly report is put together."},
        {"name": "revenue-questions", "connection": "shop",
         "description": "How to answer a question about revenue — which metric, which grouping, what to check."},
    ]  # fmt: skip
    connections, _ = await call(team(), "list_connections")
    assert connections["skills"] == ["weekly-report"]
    assert connections["connections"][0]["skills"] == ["revenue-questions"]
    async with Client(build_server(team())) as client:
        assert "`list_skills`, `get_skill`" in client.instructions


async def test_a_skill_is_served_as_written_with_its_files(team):
    answer, _ = await call(team(), "get_skill", name="revenue-questions")
    assert answer["text"] == REVENUE
    assert answer["connection"] == "shop"
    assert answer["file"] == "SKILL.md"
    assert answer["files"] == ["references/grouping.md", "references/long.txt", "scripts/check.sql"]
    assert answer["not_text"] == ["logo.png"]
    assert "next_offset" not in answer


async def test_a_file_of_a_skill_is_served_as_written(team):
    answer, _ = await call(team(), "get_skill", name="revenue-questions", file="references/grouping.md")
    assert answer["text"] == GROUPING
    assert answer["file"] == "references/grouping.md"
    assert "files" not in answer
    answer, _ = await call(team(), "get_skill", name="revenue-questions", file="scripts/check.sql")
    assert answer["text"] == CHECK
    answer, _ = await call(team(), "get_skill", name="revenue-questions", file="SKILL.md")
    assert answer["text"] == REVENUE


@pytest.mark.parametrize(
    "file",
    ["../../outside.md", "references/../../../outside.md", "/etc/passwd", ".notes.md", ".git/config", "logo.png",
     "references", "references/", "./references/grouping.md", "REFERENCES/GROUPING.MD"],
)  # fmt: skip
async def test_only_listed_files_are_served(team, file):
    text, is_error = await call(team(), "get_skill", name="revenue-questions", file=file)
    assert is_error
    assert f"has no file {file!r}" in text


@pytest.mark.parametrize("name", ["drafts", "team-skills", "../outside", "README.md", "Weekly-Report"])
async def test_only_listed_skills_are_served(team, name):
    text, is_error = await call(team(), "get_skill", name=name)
    assert is_error
    assert f"No skill named {name!r}" in text


async def test_a_long_file_comes_in_parts_that_add_up(team, shop_path):
    config = team(answer_characters=2000)
    parts, offset = [], 0
    while True:
        async with Client(build_server(config)) as client:
            arguments = {"name": "revenue-questions", "file": "references/long.txt", "offset": offset}
            result = await client.call_tool("get_skill", arguments)
        assert len(result.content[0].text) <= 2000
        answer = json.loads(result.content[0].text)
        parts.append(answer["text"])
        if "next_offset" not in answer:
            break
        offset = answer["next_offset"]
    assert len(parts) > 1
    long = shop_path.parent / "shop-skills" / "revenue-questions" / "references" / "long.txt"
    assert "".join(parts) == long.read_text()


async def test_a_list_of_many_files_is_cut_and_says_so(team, shop_path):
    references = shop_path.parent / "shop-skills" / "revenue-questions" / "references"
    for number in range(200):
        (references / f"rule-{number:03}-with-a-long-name-to-fill-the-answer.md").write_text("A rule.\n")
    async with Client(build_server(team(answer_characters=2000))) as client:
        result = await client.call_tool("get_skill", {"name": "revenue-questions"})
    assert len(result.content[0].text) <= 2000
    answer = json.loads(result.content[0].text)
    assert 0 < len(answer["files"]) < 203
    assert answer["note"].startswith(f"Only the first {len(answer['files'])} of the skill's 203 files fit")
    assert answer["text"]


async def test_an_edited_or_added_skill_is_served_without_a_restart(team, shop_path):
    config = team()
    folder = shop_path.parent / "team-skills"
    (folder / "weekly-report" / "SKILL.md").write_text(REPORT + "Changed.\n")
    (folder / "monthly-close").mkdir()
    (folder / "monthly-close" / "SKILL.md").write_text("---\nname: monthly-close\ndescription: Closing a month.\n---\n")
    answer, _ = await call(config, "get_skill", name="weekly-report")
    assert answer["text"].endswith("Changed.\n")
    listed, _ = await call(config, "list_skills")
    assert "monthly-close" in [skill["name"] for skill in listed["skills"]]


async def test_a_skill_broken_after_start_is_named(team, shop_path):
    config = team()
    (shop_path.parent / "team-skills" / "weekly-report" / "SKILL.md").write_text("no frontmatter\n")
    text, is_error = await call(config, "list_skills")
    assert is_error
    assert "The skills cannot be read" in text
    assert "weekly-report/SKILL.md: no frontmatter" in text


async def test_a_skill_of_two_connections_needs_its_connection(team, shop_path):
    body = CONFIG.replace(
        "limits:",
        '  - name: copy\n    engine: duckdb\n    path: shop.duckdb\n    readable: ["marts.*"]\n'
        "    skills: [shop-skills]\nlimits:",
    )
    config = team(body=body)
    text, is_error = await call(config, "get_skill", name="revenue-questions")
    assert is_error
    assert "a skill of more than one connection (shop, copy): give `connection`" in text
    answer, _ = await call(config, "get_skill", name="revenue-questions", connection="copy")
    assert answer["connection"] == "copy"


async def test_offset_out_of_range(team):
    text, is_error = await call(team(), "get_skill", name="weekly-report", offset=len(REPORT) + 1)
    assert is_error
    assert f"`offset` must be between 0 and {len(REPORT)}" in text


@pytest.mark.parametrize(
    ("skill", "problem"),
    [
        ("no frontmatter\n", "no frontmatter — the file must start with `---`"),
        ("---\nname: weekly-report\ndescription: unclosed\n", "no frontmatter"),
        ("---\nname: weekly-report\nname: again\ndescription: x\n---\n", "the frontmatter is not valid YAML"),
        ("---\n- a list\n---\n", "the frontmatter must be a mapping"),
        ("---\ndescription: Without a name.\n---\n", "`name` must be 1–64 lowercase letters"),
        ("---\nname: Weekly-Report\ndescription: x\n---\n", "`name` must be 1–64 lowercase letters"),
        ("---\nname: weekly--report\ndescription: x\n---\n", "none doubled"),
        ("---\nname: -weekly-report\ndescription: x\n---\n", "no hyphen at either end"),
        ("---\nname: monthly-report\ndescription: x\n---\n", "`name` must be the folder's name, 'weekly-report'"),
        ("---\nname: weekly-report\n---\n", "`description` must be text"),
        ("---\nname: weekly-report\ndescription: '  '\n---\n", "`description` must be text"),
        ("---\nname: weekly-report\ndescription: [a, b]\n---\n", "`description` must be text"),
        (f"---\nname: weekly-report\ndescription: {'x' * 1025}\n---\n", "at most 1024 characters (1025)"),
    ],
)
def test_a_skill_breaking_the_format_stops_the_server_naming_it(team, shop_path, skill, problem):
    (shop_path.parent / "team-skills" / "weekly-report" / "SKILL.md").write_text(skill)
    with pytest.raises(ConfigError) as error:
        team()
    assert "skills: " in str(error.value)
    assert "weekly-report/SKILL.md: " in str(error.value)
    assert problem in str(error.value)


def test_a_skill_that_is_not_text_stops_the_server(team, shop_path):
    (shop_path.parent / "team-skills" / "weekly-report" / "SKILL.md").write_bytes(b"---\xff\n")
    with pytest.raises(ConfigError, match=r"weekly-report/SKILL.md: not UTF-8 text \(byte 3 is not\)"):
        team()


def test_a_missing_path_or_a_folder_without_a_skill_is_named(team, shop_path):
    with pytest.raises(ConfigError) as error:
        team(body=CONFIG.replace("[team-skills]", "[nowhere]").replace("revenue-questions]", "revenue-questions/x]"))
    assert "skills: 'nowhere' does not exist" in str(error.value)
    assert "connections[0] \"shop\" → skills: 'shop-skills/revenue-questions/x' does not exist" in str(error.value)
    (shop_path.parent / "empty").mkdir()
    with pytest.raises(ConfigError, match="empty holds no skill: no SKILL.md in it or in a folder inside it"):
        team(body=CONFIG.replace("[team-skills]", "[empty]"))
    with pytest.raises(ConfigError, match="outside.md is not a folder"):
        team(body=CONFIG.replace("[team-skills]", "[outside.md]"))


def test_two_skills_with_one_name_are_named(team, shop_path):
    other = shop_path.parent / "elsewhere" / "weekly-report"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(REPORT)
    with pytest.raises(ConfigError, match="two skills are named 'weekly-report'"):
        team(body=CONFIG.replace("[team-skills]", "[team-skills, elsewhere]"))
