"""Context and skills read straight from a git repository: fetched at start (only the paths named), served with their
commit, kept fresh — a commit that breaks the checks never served — the token in a header, never repeated."""

import base64
import json
import os
import signal
import subprocess
import sys
import time
import types

import pytest
from conftest import write_config
from git_server import GitServer
from mcp import Client

from semantic_warehouse_mcp import repositories
from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

TOKEN = "kit-token-0123456789"
HEADER = "Basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
RULES = "# How revenue is counted\n\nRevenue is net of refunds.\n"
SKILL = "---\nname: revenue-questions\ndescription: How to answer a question about revenue.\n---\n\nUse the metric.\n"
FILES = {
    "docs/rules.md": RULES,
    "docs/glossary.md": "# Glossary\n",
    "skills/revenue-questions/SKILL.md": SKILL,
    "skills/revenue-questions/references/grouping.md": "# Grouping\n",
    "big/data.bin": b"\x00" * 50_000,
    "README.md": "# The kit\n",
}

CONFIG = """
repositories:
  - name: kit
    url: {url}
    ref: {ref}
    token_env: SWM_TEST_KIT_TOKEN
context:
  - {{repository: kit, path: {docs}}}
connections:
  - name: shop
    engine: duckdb
    path: shop.duckdb
    readable: ["marts.*"]
    skills:
      - {{repository: kit, path: skills/}}
"""


@pytest.fixture
def server(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    git_server = GitServer(served)
    yield git_server
    git_server.close()


@pytest.fixture
def kit(server, monkeypatch):
    monkeypatch.setenv("SWM_TEST_KIT_TOKEN", TOKEN)
    server.required = f"{HEADER}"
    return server.repository("kit", FILES)


@pytest.fixture
def team(server, kit, shop_path):
    def build(ref="main", docs="docs", body=CONFIG, reach=True):
        text = body.format(url=server.url("kit"), ref=ref, docs=docs)
        return load(write_config(shop_path.parent, text), reach=reach)

    return build


async def call(config, tool, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


def head(kit) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=kit.work, capture_output=True, text=True).stdout.strip()


async def test_context_and_skills_are_served_from_the_repository_with_their_commit(team, kit):
    config = team()
    commit = head(kit)
    listed, _ = await call(config, "list_context")
    assert [document["name"] for document in listed["documents"]] == ["docs/glossary.md", "docs/rules.md"]
    answer, _ = await call(config, "get_context", name="docs/rules.md")
    assert answer["text"] == RULES
    assert (answer["fetched_from"], answer["commit"]) == ("kit", commit)
    skill, _ = await call(config, "get_skill", name="revenue-questions")
    assert skill["text"] == SKILL
    assert skill["files"] == ["references/grouping.md"]
    assert (skill["fetched_from"], skill["commit"]) == ("kit", commit)
    connections, _ = await call(config, "list_connections")
    assert connections["context"] == ["docs/glossary.md", "docs/rules.md"]
    assert connections["connections"][0]["skills"] == ["revenue-questions"]
    [item] = connections["repositories"]
    assert {key: item[key] for key in ("name", "url", "ref", "commit", "refresh_minutes")} == {
        "name": "kit", "url": kit_url(config), "ref": "main", "commit": commit, "refresh_minutes": 10,
    }  # fmt: skip
    assert "problem" not in item


def own(run):
    """The `subprocess` of the repositories module only, its `run` replaced — the test's git server keeps its own."""
    return types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)


def kit_url(config) -> str:
    return config.repositories["kit"].url


def test_only_the_named_paths_are_fetched(team):
    config = team()
    root = config.repositories["kit"].current.root
    assert root.name == "kit"
    assert sorted(path.name for path in root.iterdir()) == ["docs", "skills"]
    git_dir = root.parent.parent / "git"
    objects = subprocess.run(
        ["git", "--git-dir", str(git_dir), "cat-file", "--batch-check", "--batch-all-objects"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert " blob 50000" not in objects  # the large file of a folder not named never came


def test_the_whole_repository_by_its_root(team):
    config = team(docs=".")
    root = config.repositories["kit"].current.root
    assert sorted(path.name for path in root.iterdir()) == ["README.md", "big", "docs", "skills"]


async def test_documents_of_the_root_are_named_after_the_repository(team):
    listed, _ = await call(team(docs="."), "list_context")
    assert "kit/README.md" in [document["name"] for document in listed["documents"]]


def test_the_token_goes_in_a_header_never_on_a_command_line(team, server, monkeypatch):
    commands = []
    run = subprocess.run

    def recording(arguments, **options):
        commands.append(arguments)
        return run(arguments, **options)

    monkeypatch.setattr(repositories, "subprocess", own(recording))
    team()
    assert commands
    assert not [argument for command in commands for argument in command if TOKEN in argument or HEADER in argument]
    assert set(server.authorizations) == {HEADER}


def test_a_refused_token_is_named_and_never_repeated(team, server):
    server.required = "Basic something-else"
    with pytest.raises(ConfigError) as error:
        team()
    message = str(error.value)
    assert "repository kit (http://127.0.0.1:" in message
    assert "the repository asks for a token: the token in the variable SWM_TEST_KIT_TOKEN was refused" in message
    assert TOKEN not in message
    assert HEADER.split()[1] not in message


def test_a_token_variable_not_set_is_named(team, monkeypatch):
    monkeypatch.delenv("SWM_TEST_KIT_TOKEN")
    with pytest.raises(ConfigError, match="the variable SWM_TEST_KIT_TOKEN is not set"):
        team()


async def test_a_symbolic_link_is_a_file_holding_its_target_never_followed(team, kit):
    kit.commit({}, symlinks={"docs/passwd.md": "/etc/passwd", "docs/up.md": "../../../outside.md"})
    config = team()
    answer, _ = await call(config, "get_context", name="docs/passwd.md")
    assert answer["text"] == "/etc/passwd"
    answer, _ = await call(config, "get_context", name="docs/up.md")
    assert answer["text"] == "../../../outside.md"
    assert not (config.repositories["kit"].current.root / "docs" / "passwd.md").is_symlink()


async def test_a_new_commit_is_served_after_a_refresh(team, kit):
    config = team()
    repository = config.repositories["kit"]
    first = repository.current
    commit = kit.commit({"docs/rules.md": "# Changed\n", "docs/new.md": "# New\n"})
    repository.refresh()
    assert repository.current.commit == commit
    assert repository.problem is None
    answer, _ = await call(config, "get_context", name="docs/rules.md")
    assert (answer["text"], answer["commit"]) == ("# Changed\n", commit)
    listed, _ = await call(config, "list_context")
    assert "docs/new.md" in [document["name"] for document in listed["documents"]]
    assert first.root.exists()  # the copy before stays for a reader still on it, until the next new commit
    kit.commit({"docs/rules.md": "# Again\n"})
    repository.refresh()
    assert not first.root.exists()


def test_a_refresh_without_a_new_commit_keeps_the_copy(team):
    config = team()
    repository = config.repositories["kit"]
    before = repository.current
    repository.refresh()
    assert repository.current is before
    assert repository.checked_at >= before.fetched_at


async def test_a_commit_breaking_the_checks_is_not_served(team, kit):
    config = team()
    repository = config.repositories["kit"]
    served = repository.current.commit
    broken = kit.commit({"skills/revenue-questions/SKILL.md": "no frontmatter\n"})
    repository.refresh()
    assert repository.current.commit == served
    assert repository.problem.startswith(f"the commit {broken[:12]} is not served, the last one stays: ")
    assert "revenue-questions/SKILL.md: no frontmatter" in repository.problem
    skill, _ = await call(config, "get_skill", name="revenue-questions")
    assert skill["text"] == SKILL
    connections, _ = await call(config, "list_connections")
    assert connections["repositories"][0]["problem"] == repository.problem
    fixed = kit.commit({"skills/revenue-questions/SKILL.md": SKILL + "Fixed.\n"})
    repository.refresh()
    assert (repository.current.commit, repository.problem) == (fixed, None)


def test_a_commit_without_a_named_path_is_not_served(team, kit):
    config = team()
    repository = config.repositories["kit"]
    served = repository.current.commit
    kit.commit({"docs/rules.md": None, "docs/glossary.md": None})
    repository.refresh()
    assert repository.current.commit == served
    assert "holds no docs at" in repository.problem


def test_an_unreachable_repository_keeps_the_copy(team, server):
    config = team()
    repository = config.repositories["kit"]
    served = repository.current
    server.down = True
    repository.refresh()
    assert repository.current is served
    assert "503" in repository.problem
    assert not repository.problem.startswith("repository kit")  # said once, where it is shown or logged


def test_a_redirect_is_not_followed(team, server, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    elsewhere = GitServer(tmp_path / "elsewhere")
    elsewhere.repository("kit", FILES)
    server.redirect = f"http://127.0.0.1:{elsewhere.http.server_port}"
    try:
        with pytest.raises(ConfigError, match="repository kit"):
            team()
    finally:
        elsewhere.close()
    assert elsewhere.headers == []  # the token went nowhere but where the configuration says


def test_git_runs_in_an_environment_of_its_own(team, server, monkeypatch):
    """The server's own environment never reaches git — not a configuration given there, not a credential."""
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'http.extraheader'='X-Leak: 1'")
    monkeypatch.setenv("GIT_ASKPASS", "/bin/false")
    environments = []
    run = subprocess.run

    def recording(arguments, **options):
        if arguments[1:] != ["--version"]:
            environments.append(options["env"])
        return run(arguments, **options)

    monkeypatch.setattr(repositories, "subprocess", own(recording))
    team()
    assert server.headers and not [headers for headers in server.headers if "X-Leak" in headers]
    for environment in environments:
        assert "GIT_CONFIG_PARAMETERS" not in environment and "GIT_ASKPASS" not in environment
        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
        assert environment["HOME"] != os.environ.get("HOME")
        settings = {environment[f"GIT_CONFIG_KEY_{i}"]: environment[f"GIT_CONFIG_VALUE_{i}"]
                    for i in range(int(environment["GIT_CONFIG_COUNT"]))}  # fmt: skip
        assert settings["core.hooksPath"] == "/dev/null"
        assert settings["credential.helper"] == ""


def test_a_protocol_but_https_is_refused_by_git_too(tmp_path, kit):
    """Past the configuration's check of the URL, git itself is told to use no other protocol."""
    repository = repositories.Repository(name="kit", url=f"file://{kit.bare}", paths=["docs"])
    with pytest.raises(repositories.RepositoryError, match="transport 'file' not allowed"):
        repository.start()


def test_a_ref_is_never_read_as_an_option(tmp_path, server, kit):
    """Past the configuration's check of the ref, git is told where its options end."""
    planted = tmp_path / "planted"
    repository = repositories.Repository(
        name="kit", url=server.url("kit"), ref=f"--upload-pack=touch {planted}", token_env="SWM_TEST_KIT_TOKEN",
        paths=["docs"],
    )  # fmt: skip
    with pytest.raises(repositories.RepositoryError):
        repository.start()
    assert not planted.exists()


def test_the_token_is_never_repeated_from_what_git_says(monkeypatch):
    monkeypatch.setenv("SWM_TEST_KIT_TOKEN", TOKEN)
    repository = repositories.Repository(name="kit", url="https://git.example.com/kit.git",
                                         token_env="SWM_TEST_KIT_TOKEN")  # fmt: skip
    said = repository._hint(f"fatal: unable to access with {TOKEN} or {HEADER.split()[1]}: 403")
    assert TOKEN not in said and HEADER.split()[1] not in said
    assert said.endswith("the repository asks for a token: the token in the variable SWM_TEST_KIT_TOKEN was refused")


async def test_a_tag_or_a_commit_as_the_ref(team, kit):
    first = head(kit)
    kit.tag("v1")
    kit.commit({"docs/rules.md": "# Later\n"})
    for ref in ("v1", first):
        config = team(ref=ref)
        assert config.repositories["kit"].current.commit == first
        answer, _ = await call(config, "get_context", name="docs/rules.md")
        assert answer["text"] == RULES
    assert repositories.keep_fresh([team(ref=first).repositories["kit"]]) is None  # a commit never changes


def test_without_a_ref_the_default_branch(team, kit):
    config = team(body=CONFIG.replace("    ref: {ref}\n", ""))
    assert config.repositories["kit"].current.commit == head(kit)


def test_a_path_the_repository_does_not_hold_is_named(team, kit):
    with pytest.raises(ConfigError) as error:
        team(docs="handbook")
    assert "repository kit (http://127.0.0.1:" in str(error.value)
    assert f"holds no handbook at {head(kit)[:12]}" in str(error.value)


def test_a_ref_the_repository_does_not_hold_is_named(team):
    with pytest.raises(ConfigError, match="couldn't find remote ref nowhere"):
        team(ref="nowhere")


def test_the_offline_check_fetches_nothing(team, server):
    config = team(reach=False)
    assert config.repositories["kit"].current is None
    assert server.authorizations == []


def test_a_skill_breaking_the_format_in_the_repository_stops_the_server(team, kit):
    kit.commit({"skills/revenue-questions/SKILL.md": "---\nname: Revenue\ndescription: x\n---\n"})
    with pytest.raises(ConfigError, match=r"\"shop\" → skills: .*revenue-questions/SKILL.md: `name` must be"):
        team()


def test_git_missing_is_named(team, monkeypatch):
    monkeypatch.setattr(repositories.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="repositories: the program `git` is not installed"):
        team()


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("url: {url}", "url: https://someone:hunter2@git.example.com/kit.git"), "url: holds credentials"),
        (("url: {url}", "url: http://git.example.com/kit.git"), "url: must be https:// — http:// only to this machine"),
        (("url: {url}", "url: ssh://git.example.com/kit.git"), "url: must be an https:// URL"),
        (("url: {url}", "url: /srv/git/kit.git"), "url: must be an https:// URL"),
        (("ref: {ref}", "ref: --upload-pack=touch"), "ref: '--upload-pack=touch' is not a branch, a tag or a commit"),
        (("ref: {ref}", "ref: 'main; rm'"), "is not a branch, a tag or a commit"),
        (("path: {docs}", "path: ../docs"), "'../docs' in kit must be a path inside the repository"),
        (("path: {docs}", "path: /etc"), "'/etc' in kit must be a path inside the repository"),
        (("path: {docs}", "path: -docs"), "must be a path inside the repository"),
        (("{{repository: kit, path: {docs}}}", "{{repository: kti, path: docs}}"), "no repository named 'kti'"),
        (("token_env: SWM_TEST_KIT_TOKEN", "token_env: $TOKEN"), "token_env"),
        (("ref: {ref}", "ref: {ref}\n    refresh_minutes: -1"), "refresh_minutes"),
    ],
)
def test_a_wrong_repository_entry_is_named(team, change, problem):
    body = CONFIG.replace(*change)
    with pytest.raises(ConfigError) as error:
        team(body=body)
    assert problem in str(error.value)
    assert "hunter2" not in str(error.value)


def test_a_repository_named_by_no_entry_or_twice_is_named(team):
    unused = CONFIG.replace(
        "repositories:\n", "repositories:\n  - {{name: spare, url: 'https://git.example.com/x.git'}}\n"
    )
    with pytest.raises(ConfigError, match="repositories: spare is named by no `context` or `skills` entry"):
        team(body=unused)
    twice = CONFIG.replace(
        "repositories:\n", "repositories:\n  - {{name: kit, url: 'https://git.example.com/x.git'}}\n"
    )
    with pytest.raises(ConfigError, match="another repository has this name"):
        team(body=twice)


# --- found by the second reader -------------------------------------------------------------------------------------


def test_what_git_says_in_no_encoding_is_a_problem_never_a_crash(team, server):
    server.broken = b"maintenance \xff\xfe\n"
    with pytest.raises(ConfigError, match="repository kit"):
        team()
    server.broken = None
    config = team()
    repository = config.repositories["kit"]
    served = repository.current
    server.broken = b"maintenance \xff\xfe\n"
    repository.refresh()
    assert repository.current is served
    assert repository.problem.endswith("returned error: 503")  # read, not crashed on


def test_a_refresh_that_meets_the_unexpected_says_so_and_keeps_the_copy(team, monkeypatch):
    config = team()
    repository = config.repositories["kit"]
    served = repository.current

    def broken(*arguments):
        raise ValueError("something no one foresaw")

    monkeypatch.setattr(repository, "_git", broken)
    repository.refresh()  # never raises: the thread that refreshes must outlive it
    assert repository.current is served
    assert repository.problem == "an unexpected error: ValueError: something no one foresaw"


async def test_a_folder_and_a_path_inside_it(team):
    body = CONFIG.replace(
        '    readable: ["marts.*"]\n',
        '    readable: ["marts.*"]\n    context: [{{repository: kit, path: docs/rules.md}}]\n',
    )
    config = team(body=body)
    answer, _ = await call(config, "get_context", name="rules.md", connection="shop")
    assert answer["text"] == RULES


def test_a_token_written_for_its_variable_is_never_repeated(team, monkeypatch):
    pasted = "ghp_aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z"
    with pytest.raises(ConfigError) as error:
        team(body=CONFIG.replace("token_env: SWM_TEST_KIT_TOKEN", f"token_env: {pasted}"))
    assert pasted not in str(error.value)
    assert "the variable named in `token_env` (not repeated" in str(error.value)


def test_a_token_in_the_urls_query_is_refused_never_repeated(team):
    with pytest.raises(ConfigError) as error:
        team(body=CONFIG.replace("url: {url}", "url: https://git.example.com/kit.git?private_token=glpat-secret"))
    assert "url: must hold no query or fragment" in str(error.value)
    assert "glpat-secret" not in str(error.value)


@pytest.mark.parametrize("path", ["./docs", "docs/.", "docs//shop", "docs/./shop", "/docs", ""])
def test_a_path_with_an_empty_or_dot_part_is_refused(team, path):
    with pytest.raises(ConfigError, match="must be a path inside the repository, as `docs/shop`"):
        team(docs=f"'{path}'")


def test_a_path_ending_with_a_slash_is_the_folder(team):
    config = team(docs="docs/")
    assert sorted(path.name for path in config.repositories["kit"].current.root.iterdir()) == ["docs", "skills"]


def test_an_uppercase_scheme_is_refused_by_the_check(team):
    with pytest.raises(ConfigError, match="url: must be an https:// URL"):
        team(body=CONFIG.replace("url: {url}", "url: HTTPS://git.example.com/kit.git"))


def test_an_unknown_key_of_an_entry_is_named_with_the_likely_one(team):
    with pytest.raises(ConfigError) as error:
        team(body=CONFIG.replace("{{repository: kit, path: {docs}}}", "{{repository: kit, paht: docs}}"))
    assert "context[0] → paht: unknown key — did you mean 'path'?" in str(error.value)
    assert "valid string" not in str(error.value)


def test_an_empty_repository_is_named(server, shop_path, monkeypatch):
    monkeypatch.setenv("SWM_TEST_KIT_TOKEN", TOKEN)
    from git_server import git

    git("init", "-q", "--bare", "--initial-branch=main", str(server.root / "empty.git"), cwd=server.root)
    body = CONFIG.format(url=server.url("empty"), ref="main", docs="docs").replace("    ref: main\n", "")
    with pytest.raises(ConfigError, match="no branch, tag or commit of that name"):
        load(write_config(shop_path.parent, body))


def test_an_uppercase_commit_is_never_looked_for_again(team, kit):
    config = team(ref=head(kit).upper())
    assert repositories.keep_fresh([config.repositories["kit"]]) is None


def test_what_git_prints_in_no_encoding_is_read_never_crashed_on(team, kit):
    """Names in a commit may be in any encoding: git's output is read as UTF-8 with what is not replaced."""
    kit.commit({os.fsdecode(b"docs/caf\xff.md"): "# Caf\n"})
    repository = team().repositories["kit"]
    git_dir = repository.current.root.parent.parent / "git"
    commit = repository.current.commit
    listed = repository._git("--git-dir", str(git_dir), "ls-tree", "-r", "--name-only", "-z", commit)  # names raw
    assert "docs/caf\ufffd.md" in listed.split("\0")


async def test_a_name_that_is_not_utf8_is_left_out(team, kit):
    kit.commit({os.fsdecode(b"docs/caf\xff.md"): "# Caf\n"})
    listed, _ = await call(team(), "list_context")
    assert [document["name"] for document in listed["documents"]] == ["docs/glossary.md", "docs/rules.md"]


def test_the_offline_check_needs_no_git(team, monkeypatch):
    monkeypatch.setattr(repositories.shutil, "which", lambda name: None)
    assert team(reach=False).repositories["kit"].current is None


def test_a_proxy_never_carries_the_token_in_clear_text(team, server, tmp_path, monkeypatch):
    (tmp_path / "proxy").mkdir()
    proxy = GitServer(tmp_path / "proxy")
    for variable in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(variable, raising=False)
    for variable in ("http_proxy", "HTTP_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(variable, f"http://127.0.0.1:{proxy.http.server_port}")
    try:
        team()
    finally:
        proxy.close()
    assert proxy.headers == []


def test_every_entry_is_named_at_once_with_a_repository(team, shop_path):
    body = CONFIG.replace("path: {docs}}}", "path: handbook}}").replace('readable: ["marts.*"]', 'readable: ["marts"]')
    with pytest.raises(ConfigError) as error:
        team(body=body)
    assert "holds no handbook" in str(error.value)
    assert "readable: 'marts' must be schema.table" in str(error.value)


@pytest.mark.parametrize("http", [False, True])
def test_the_copies_are_removed_when_the_server_is_stopped(team, shop_path, tmp_path, http):
    team()  # the configuration, written and fetched once to know it is right
    folder = tmp_path / f"tmp-{http}"
    folder.mkdir()
    extra = ["--http", "--port", "0"] if http else []
    process = subprocess.Popen(
        [sys.executable, "-m", "semantic_warehouse_mcp.server", "--config", str(shop_path.parent / "config.yaml"),
         *extra],
        env={**os.environ, "TMPDIR": str(folder)}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )  # fmt: skip
    deadline = time.monotonic() + 30
    while not any(folder.iterdir()) or not any(next(folder.iterdir()).rglob("SKILL.md")):
        assert time.monotonic() < deadline and process.poll() is None, process.stderr.read()
        time.sleep(0.1)
    time.sleep(1)  # serving
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=30)
    assert list(folder.iterdir()) == []
