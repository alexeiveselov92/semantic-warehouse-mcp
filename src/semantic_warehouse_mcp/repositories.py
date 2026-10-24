"""Repositories: context and skills read straight from a git repository — fetched by the server when it starts, kept
fresh in the background, the token by its variable's name (docs/reference/configuration.md, "Repositories").

Only the folders the configuration names are fetched (a partial, one-commit fetch, then those paths checked out), by
the `git` program in an environment of the server's own: no system or user configuration, no credential helper, no
prompt, no redirect followed, symbolic links written as plain files — so nothing in a repository reaches outside its
copy. The token goes in a header through the environment, never on a command line, and is never repeated."""

from __future__ import annotations

import atexit
import base64
import datetime
import ipaddress
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from . import files

# The oldest git that takes its configuration from the environment (GIT_CONFIG_COUNT).
MINIMUM_GIT = (2, 31)
# The longest one git command may run: a fetch past it is stopped and is a problem like any other.
GIT_SECONDS = 300
# What a deployed server may need to reach a repository at all: a proxy, its own certificates.
PASSED_THROUGH = (
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH",
)  # fmt: skip
# A ref as the configuration may write it: a branch, a tag or a commit — never an option.
REF = re.compile(r"^(?!-)[A-Za-z0-9._/-]+$")
COMMIT = re.compile(r"^([0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
PROXIES = {"HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"}


class RepositoryError(Exception):
    pass


def url_problem(url: str) -> str | None:
    """What is wrong with a repository's URL, or None: https, or http to this machine only; no credentials in it —
    neither a user and password nor a query."""
    parsed = urllib.parse.urlsplit(url)
    if not url.startswith(("https://", "http://")) or not parsed.hostname:
        return "must be an https:// URL"
    if "@" in parsed.netloc:
        return "holds credentials: name the variable holding the token in `token_env` instead"
    if "?" in url or "#" in url:
        return "must hold no query or fragment (`?…`, `#…`): a token goes in `token_env`"
    if parsed.scheme == "http" and not _loopback(parsed.hostname):
        return "must be https:// — http:// only to this machine (localhost)"
    return None


def path_problem(path: str) -> str | None:
    """What is wrong with a path inside a repository, or None: `.`, or names joined by `/` — none empty, `.` or `..`."""
    inside = normal(path)
    if inside == ".":
        return None
    if "\\" in inside or inside.startswith("-") or any(part in ("", ".", "..") for part in inside.split("/")):
        return "must be a path inside the repository, as `docs/shop` — no `.`, `..` or empty part; `.` for all of it"
    return None


def normal(path: str) -> str:
    """A path inside a repository as it is fetched and named: without the `/` that may end it."""
    return path[:-1] if path.endswith("/") and len(path) > 1 else path


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Copy:
    """A fetched commit of a repository: its folders, checked out under `root` — a folder named after the repository."""

    root: pathlib.Path
    commit: str
    fetched_at: datetime.datetime


@dataclass(eq=False)
class Repository:
    name: str
    url: str
    ref: str | None = None  # None: the branch the repository's HEAD names
    token_env: str | None = None
    token_user: str = "x-access-token"
    refresh_minutes: int = 10
    # The paths inside it the configuration names — only these are fetched.
    paths: list[str] = field(default_factory=list)
    # The copy served, swapped whole by a refresh; None until fetched.
    current: Copy | None = None
    # When a refresh last reached the repository; what went wrong the last time it did not, or None.
    checked_at: datetime.datetime | None = None
    problem: str | None = None
    # The problems the configuration's checks find with a copy at a root (set by the configuration): a refresh serves
    # a new commit only when there are none.
    check: Callable[[pathlib.Path], list[str]] = lambda root: []
    _previous: Copy | None = None
    _generation: int = 0
    _home: pathlib.Path | None = None

    @property
    def described(self) -> str:
        return f"repository {self.name} ({self.url}{', ref ' + self.ref if self.ref else ''})"

    def copy_of(self, file: pathlib.Path) -> Copy | None:
        """The copy a file is in — the one served, or the one before it, which a reader may still be on."""
        for copy in (self.current, self._previous):
            if copy is not None and file.is_relative_to(copy.root):
                return copy
        return None

    def start(self) -> None:
        """Fetch the repository and serve it. Raises RepositoryError saying what went wrong."""
        try:
            self.current = self._copy(self._fetch())
        except OSError as error:
            raise RepositoryError(f"its copy cannot be written: {error.strerror or error}") from None
        self.checked_at = self.current.fetched_at

    def refresh(self) -> None:
        """Fetch the ref anew and serve a new commit when it passes the checks; on any problem the copy served stays,
        and `problem` says what went wrong. Never raises: the thread that refreshes outlives what a fetch meets."""
        try:
            commit = self._fetch()
            if self.current is not None and commit == self.current.commit:
                self.checked_at, self.problem = _now(), None
                return
            copy = self._copy(commit)
            problems = self.check(copy.root)
        except RepositoryError as error:
            self._failed(str(error))
            return
        except Exception as error:  # noqa: BLE001 — said, never raised: the commit served stays
            self._failed(self._hint(f"an unexpected error: {type(error).__name__}: {error}"))
            return
        if problems:
            shutil.rmtree(copy.root.parent, ignore_errors=True)
            self._failed(f"the commit {commit[:12]} is not served, the last one stays: {'; '.join(problems)}")
            return
        if self._previous is not None:  # the copy before the one served now: no reader is left on it
            shutil.rmtree(self._previous.root.parent, ignore_errors=True)
        self._previous, self.current = self.current, copy
        self.checked_at, self.problem = copy.fetched_at, None
        _say(f"{self.described}: now serving {commit[:12]}")

    def _failed(self, problem: str) -> None:
        self.problem = problem
        _say(f"{self.described}: {problem}")

    def _fetch(self) -> str:
        """The commit the ref names now, fetched — a single commit, its trees, no file yet."""
        git_dir = self._folder() / "git"
        if not git_dir.exists():
            self._git("init", "-q", "--bare", "--template=", str(git_dir))
            self._git("--git-dir", str(git_dir), "remote", "add", "origin", "--end-of-options", self.url)
        ref = self.ref or "HEAD"
        self._git("--git-dir", str(git_dir), "fetch", "-q", "--depth", "1", "--filter=blob:none", "--no-tags",
                  "--end-of-options", "origin", ref)  # fmt: skip
        return self._git("--git-dir", str(git_dir), "rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}").strip()

    def _copy(self, commit: str) -> Copy:
        """The configuration's paths of a fetched commit, checked out in a folder of their own; a path the commit does
        not hold is named."""
        git_dir = self._folder() / "git"
        paths = sorted({normal(path) for path in self.paths}) or ["."]
        missing = [
            path
            for path in paths
            if path != "."  # one path at a time: with a folder and a path inside it, git lists only the inner one
            and path
            not in self._git("--git-dir", str(git_dir), "ls-tree", "--name-only", "-z", commit, "--", path).split("\0")
        ]
        if missing:
            raise RepositoryError(f"it holds no {', '.join(missing)} at {commit[:12]}")
        self._generation += 1
        root = self._folder() / str(self._generation) / self.name
        root.mkdir(parents=True)
        try:
            self._git("--git-dir", str(git_dir), "--work-tree", str(root), "checkout", "-q", commit, "--", *paths)
        except RepositoryError:
            shutil.rmtree(root.parent, ignore_errors=True)
            raise
        return Copy(root=root, commit=commit, fetched_at=_now())

    def _folder(self) -> pathlib.Path:
        """This repository's own folder in the process's cache: its git data, and a folder per copy."""
        if self._home is None:
            self._home = pathlib.Path(tempfile.mkdtemp(prefix=f"{self.name}-", dir=_cache()))
        return self._home

    def _git(self, *arguments: str) -> str:
        token = self._token()
        settings = {
            "core.symlinks": "false",  # a link in the repository is a file holding its target, never followed
            "core.hooksPath": "/dev/null",
            "credential.helper": "",
            "http.followRedirects": "false",  # a header with the token never goes to another host
            "protocol.allow": "never",
            "protocol.https.allow": "always",
        }
        if self.url.startswith("http://"):
            settings["protocol.http.allow"] = "always"  # to this machine only (`url_problem`)
        if token is not None:
            pair = base64.b64encode(f"{self.token_user}:{token}".encode()).decode()
            settings["http.extraHeader"] = f"Authorization: Basic {pair}"
        passed = PASSED_THROUGH if self.url.startswith("https://") else set(PASSED_THROUGH) - PROXIES  # no clear text
        environment = {key: os.environ[key] for key in passed if key in os.environ}
        environment |= {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(_cache()),
            "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": str(len(settings)),
        }
        for index, (key, value) in enumerate(settings.items()):
            environment[f"GIT_CONFIG_KEY_{index}"] = key
            environment[f"GIT_CONFIG_VALUE_{index}"] = value
        try:
            done = subprocess.run(["git", *arguments], env=environment, capture_output=True, timeout=GIT_SECONDS,
                                  check=False)  # fmt: skip
        except subprocess.TimeoutExpired:
            raise RepositoryError(f"git did not finish in {GIT_SECONDS} s") from None
        except OSError as error:
            raise RepositoryError(f"git cannot be run: {error.strerror or error}") from None
        if done.returncode != 0:  # what git says may be in any encoding a server or a file name brings
            stderr = done.stderr.decode("utf-8", "replace")
            said = [line.strip() for line in stderr.splitlines() if line.strip()]
            raise RepositoryError(self._hint(said[-1] if said else f"git ended with {done.returncode}"))
        return done.stdout.decode("utf-8", "replace")

    def _hint(self, said: str) -> str:
        """git's own words, the token never among them, with what to do when the server asked for a token."""
        token = os.environ.get(self.token_env) if self.token_env else None
        if token:
            pair = base64.b64encode(f"{self.token_user}:{token}".encode()).decode()
            said = said.replace(pair, "…").replace(token, "…")
        if "could not read Username" in said or "Authentication failed" in said or " 401" in said or " 403" in said:
            asked = (
                f"the token in the variable {files.variable(self.token_env, '`token_env`')} was refused"
                if self.token_env
                else "set `token_env`"
            )
            said += f" — the repository asks for a token: {asked}"
        elif "couldn't find remote ref" in said:
            said += " — no branch, tag or commit of that name (a commit by its full hash; an empty repository has none)"
        return said

    def _token(self) -> str | None:
        if self.token_env is None:
            return None
        token = os.environ.get(self.token_env)
        if not token:
            raise RepositoryError(f"the variable {files.variable(self.token_env, '`token_env`')} is not set")
        return token


@dataclass(frozen=True)
class Place:
    """A file or folder of context or skills as the configuration names it: on disk, or inside a repository's copy."""

    written: str
    local: pathlib.Path | None = None
    repository: Repository | None = None
    path: str = "."

    def current(self, copies: dict[str, pathlib.Path] | None = None) -> pathlib.Path:
        """Where it is now — in a repository, in the copy served, or the one `copies` gives for its repository."""
        if self.repository is None:
            assert self.local is not None
            return self.local
        root = (copies or {}).get(self.repository.name)
        if root is None:
            if self.repository.current is None:
                raise RepositoryError(f"{self.repository.described} is not fetched")
            root = self.repository.current.root
        return root if normal(self.path) == "." else root / normal(self.path)


def current(places: tuple[Place, ...], copies: dict[str, pathlib.Path] | None = None) -> tuple[pathlib.Path, ...]:
    return tuple(place.current(copies) for place in places)


def git_problem() -> str | None:
    """What keeps this machine from fetching a repository, or None: git missing, or older than MINIMUM_GIT."""
    if shutil.which("git") is None:
        return "the program `git` is not installed, and repositories are fetched with it"
    done = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    found = re.search(r"(\d+)\.(\d+)", done.stdout)
    if found is None or (int(found[1]), int(found[2])) < MINIMUM_GIT:
        wanted = ".".join(map(str, MINIMUM_GIT))
        return f"git {wanted} or later is needed to fetch repositories ({done.stdout.strip() or 'version unknown'})"
    return None


def keep_fresh(repositories: list[Repository]) -> threading.Thread | None:
    """Refresh each repository every `refresh_minutes`, in the background, for as long as the server runs."""
    due = [repository for repository in repositories if repository.refresh_minutes > 0]
    due = [repository for repository in due if not (repository.ref and COMMIT.match(repository.ref))]
    if not due:
        return None

    def loop() -> None:
        next_at = {repository.name: time.monotonic() + repository.refresh_minutes * 60 for repository in due}
        while True:
            time.sleep(max(0.0, min(next_at.values()) - time.monotonic()))
            for repository in due:
                if next_at[repository.name] <= time.monotonic():
                    repository.refresh()
                    next_at[repository.name] = time.monotonic() + repository.refresh_minutes * 60

    thread = threading.Thread(target=loop, name="repositories", daemon=True)
    thread.start()
    return thread


_CACHE: pathlib.Path | None = None


def _cache() -> pathlib.Path:
    """The folder this process keeps its copies in — a temporary one, removed when it ends."""
    global _CACHE
    if _CACHE is None:
        _CACHE = pathlib.Path(tempfile.mkdtemp(prefix="semantic-warehouse-mcp-"))
        atexit.register(remove_copies)
    return _CACHE


def remove_copies() -> None:
    """Remove every copy this process fetched — when it ends, or is stopped."""
    if _CACHE is not None:
        shutil.rmtree(_CACHE, ignore_errors=True)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _say(message: str) -> None:
    print(f"semantic-warehouse-mcp: {message}", file=sys.stderr)
