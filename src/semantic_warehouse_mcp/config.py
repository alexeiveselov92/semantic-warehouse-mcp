"""The configuration file and its check: a wrong entry stops the server, naming it (docs/reference/configuration.md)."""

from __future__ import annotations

import difflib
import pathlib
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import files, guard, repositories, semantic, skills, vocabulary
from .adapters import ENGINES, Adapter, AdapterError, adapter_class
from .context import ContextError, documents
from .repositories import Place, Repository
from .semantic import Model
from .vocabulary import Vocabulary

# A readable pattern or a large table: schema.table, either part may hold * or ? globs.
TWO_PARTS = re.compile(r"^[^.\s]+\.[^.\s]+$")


class ConfigError(Exception):
    """Every problem found, one per line, each naming its entry — a password written inside a URL left out."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = [files.redact(problem) for problem in problems]
        problems = self.problems
        lines = "\n".join(f"  - {problem}" for problem in problems)
        noun = "problem" if len(problems) == 1 else "problems"
        super().__init__(f"the configuration {source} has {len(problems)} {noun}:\n{lines}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LargeTableEntry(_Strict):
    table: str
    bound_by: Annotated[str, Field(min_length=1)]
    narrowed_by: list[Annotated[str, Field(min_length=1)]] = []


class InRepository(_Strict):
    repository: str
    path: str = "."


class RepositoryEntry(_Strict):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
    url: str
    ref: str | None = None
    token_env: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")] | None = None
    token_user: str = "x-access-token"
    refresh_minutes: Annotated[int, Field(ge=0, le=1440)] = 10


class ConnectionLimits(_Strict):
    rows: Annotated[int, Field(gt=0, le=100_000)] = 500
    seconds: Annotated[float, Field(gt=0, le=3600)] = 30


class ConnectionEntry(BaseModel):
    # The engine's own keys (a path, a URL, …) are checked against its adapter's settings, in `parse`.
    model_config = ConfigDict(extra="ignore", strict=True)

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
    engine: str
    description: str | None = None
    readable: Annotated[list[str], Field(min_length=1)]
    large_tables: list[LargeTableEntry] = []
    limits: ConnectionLimits = ConnectionLimits()
    instructions: str | None = None
    context: list[str | InRepository] = []
    skills: list[str | InRepository] = []
    semantic_models: list[str] = []
    missing_tables: Literal["refuse", "leave_out"] = "refuse"


class ServerLimits(_Strict):
    answer_characters: Annotated[int, Field(ge=1000, le=1_000_000)] = 40_000


class ConfigFile(_Strict):
    connections: Annotated[list[ConnectionEntry], Field(min_length=1)]
    limits: ServerLimits = ServerLimits()
    instructions: str | None = None
    context: list[str | InRepository] = []
    skills: list[str | InRepository] = []
    vocabularies: list[str] = []
    repositories: list[RepositoryEntry] = []


@dataclass(frozen=True)
class Connection:
    name: str
    engine: str
    description: str | None
    readable: tuple[str, ...]
    large_tables: tuple[LargeTableEntry, ...]
    limits: ConnectionLimits
    adapter: Adapter
    rules: guard.Rules
    instructions: str | None = None
    context: tuple[Place, ...] = ()
    skills: tuple[Place, ...] = ()
    models: tuple[Model, ...] = ()
    # With `missing_tables: leave_out`: each dataset left out because the database holds no table of its source.
    left_out: tuple[str, ...] = ()
    # What the connection's check found worth saying to whoever runs the server, without stopping it.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    source: str
    connections: dict[str, Connection]
    limits: ServerLimits
    instructions: str | None = None
    context: tuple[Place, ...] = ()
    skills: tuple[Place, ...] = ()
    # By name: where context and skills are read from when they are not on disk.
    repositories: dict[str, Repository] = field(default_factory=dict)
    # By vendor name, one for the whole server: a vendor's keys mean the same in every model that uses them.
    vocabularies: dict[str, Vocabulary] = field(default_factory=dict)


def load(path: str | pathlib.Path, reach: bool = True) -> Config:
    """Read and check a configuration file, then reach every connection (with `reach`). Raises ConfigError naming
    each problem."""
    path = pathlib.Path(path)
    source = str(path)
    try:
        data = files.load_file(path)
    except files.FileProblem as problem:
        raise ConfigError(source, [str(problem)]) from problem
    return parse(data, source=source, base_dir=path.resolve().parent, reach=reach)


def parse(data: Any, *, source: str, base_dir: pathlib.Path, reach: bool = True) -> Config:
    """Check configuration data already read from YAML; with `reach`, also open every connection."""
    if not isinstance(data, dict):
        raise ConfigError(source, ["the file must hold a mapping with `connections:` at the top"])
    problems: list[str] = []
    parsed: ConfigFile | None = None
    try:
        parsed = ConfigFile.model_validate(data)
    except ValidationError as error:
        problems.extend(files.describe(error, data, ConfigFile))

    # A connection's keys are the common ones and its engine's: checked here, so a typo is named with the rest.
    raw_connections = data.get("connections")
    settings: list[Any] = []
    for index, raw in enumerate(raw_connections if isinstance(raw_connections, list) else []):
        if not isinstance(raw, dict):
            settings.append(None)
            continue
        where = files.location(("connections", index), data)
        cls = adapter_class(raw["engine"]) if isinstance(raw.get("engine"), str) else None
        engine_keys = list(cls.settings_model.model_fields) if cls else []
        known = list(ConnectionEntry.model_fields) + engine_keys
        for key in raw:
            if key not in known:
                hint = difflib.get_close_matches(str(key), known, n=1)
                suffix = f" — did you mean {hint[0]!r}?" if hint else f" (known: {', '.join(known)})"
                problems.append(f"{where} → {key}: unknown key{suffix}")
        if isinstance(raw.get("engine"), str) and cls is None:
            supported = ", ".join(sorted(ENGINES))
            problems.append(f"{where} → engine: {raw['engine']!r} is not supported (supported: {supported})")
        if cls is None:
            settings.append(None)
            continue
        try:
            settings.append(cls.settings_model.model_validate({k: v for k, v in raw.items() if k in engine_keys}))
        except ValidationError as error:
            problems.extend(f"{where} → {line}" for line in files.describe(error, raw, cls.settings_model))
            settings.append(None)
    if parsed is None or problems:
        raise ConfigError(source, problems)

    vocabularies, vocabulary_problems = _vocabularies(parsed.vocabularies, base_dir)
    problems.extend(vocabulary_problems)
    repository_list, repository_problems = _repositories(parsed.repositories, data, reach)
    problems.extend(repository_problems)
    by_name = {repository.name: repository for repository in repository_list}
    # Each scope's context and skills, checked once the repositories they name are fetched: (where, key, places).
    scopes: list[tuple[str, str, tuple[Place, ...]]] = []
    place_problems: list[str] = []
    connections: dict[str, Connection] = {}
    for index, entry in enumerate(parsed.connections):
        where = files.location(("connections", index), data)
        if entry.name in connections:
            problems.append(f"{where}: another connection has this name")
            continue
        table_problems = _check_tables(entry)
        context_places, context_problems = _places(entry.context, base_dir, by_name, "context")
        skill_places, skill_problems = _places(entry.skills, base_dir, by_name, "skills")
        scopes += [(f"{where} → ", "context", context_places), (f"{where} → ", "skills", skill_places)]
        place_problems += context_problems + skill_problems
        models, model_problems = _models(entry.semantic_models, base_dir)
        entry_problems = table_problems + context_problems + skill_problems + model_problems
        if entry_problems:
            problems.extend(f"{where} → {problem}" for problem in entry_problems)
            continue
        adapter = adapter_class(entry.engine)(settings[index], base_dir)
        if reach:
            try:
                adapter.check()
            except AdapterError as error:
                problems.append(f"{where}: {error}")
                continue
        rules = _rules(entry, adapter)  # after `check`, which may learn the connection's own catalog
        if reach:
            note = _beyond_readable(adapter, rules)
            if note:
                adapter.notes.append(note)
        missing: list[tuple[Model, str, str]] = []
        leave_out = entry.missing_tables == "leave_out"
        binding_problems = _bind(models, rules, adapter if reach else None, missing if leave_out else None)
        binding_problems += _own_metadata(models, vocabularies)
        if binding_problems:
            problems.extend(f"{where} → semantic_models → {problem}" for problem in binding_problems)
            continue
        connections[entry.name] = Connection(
            name=entry.name,
            engine=entry.engine,
            description=entry.description,
            readable=tuple(entry.readable),
            large_tables=tuple(entry.large_tables),
            limits=entry.limits,
            adapter=adapter,
            rules=rules,
            instructions=entry.instructions,
            context=context_places,
            skills=skill_places,
            models=tuple(models),
            left_out=_left_out(missing),
            notes=tuple(adapter.notes),
        )
    server_context, server_context_problems = _places(parsed.context, base_dir, by_name, "context")
    server_skills, server_skill_problems = _places(parsed.skills, base_dir, by_name, "skills")
    problems += server_context_problems + server_skill_problems
    place_problems += server_context_problems + server_skill_problems
    scopes += [("", "context", server_context), ("", "skills", server_skills)]
    if not repository_problems:  # fetched whatever else is wrong, so every problem is named at once
        problems += _fetch(repository_list, scopes, reach, every_entry_read=not place_problems)
    if problems:
        raise ConfigError(source, problems)
    return Config(
        source=source,
        connections=connections,
        limits=parsed.limits,
        instructions=parsed.instructions,
        context=server_context,
        skills=server_skills,
        repositories=by_name,
        vocabularies=vocabularies,
    )


def _beyond_readable(adapter: Adapter, rules: guard.Rules) -> str | None:
    """A note when the connection's user may read tables `readable` leaves out: the guard holds a query to `readable`,
    but a function defined in the database reads with the user's grants, where the guard cannot see."""
    try:
        granted = adapter.granted_tables()
    except AdapterError as error:
        return f"the tables the user may read could not be listed: {error}"
    if granted is None:
        return None
    beyond = [f"{schema}.{table}" for schema, table in granted if not guard.is_readable(schema, table, rules)]
    if not beyond:
        return None
    shown = ", ".join(beyond[:3]) + (f" and {len(beyond) - 3} more" if len(beyond) > 3 else "")
    return (
        f"the user may read {len(beyond)} table{'s' if len(beyond) > 1 else ''} `readable` leaves out ({shown}): "
        "queries are held to `readable`, but a function defined in the database reads with the user's grants — grant "
        "the user only what agents may read"
    )


def _rules(entry: ConnectionEntry, adapter: Adapter) -> guard.Rules:
    """What the guard enforces on a connection: its configuration, and its engine's dialect and functions."""
    return guard.Rules(
        dialect=adapter.dialect,
        default_schema=adapter.default_schema,
        readable=tuple(entry.readable),
        large_tables=tuple(
            guard.LargeTable(large.table, large.bound_by, tuple(large.narrowed_by)) for large in entry.large_tables
        ),
        catalog=adapter.catalog,
        allowed_table_functions=adapter.allowed_table_functions,
        denied_functions=adapter.denied_functions,
        table_reading_functions=adapter.table_reading_functions,
        aliases_shadow_columns=adapter.aliases_shadow_columns,
    )


def _repositories(entries: list[RepositoryEntry], data: Any, reach: bool) -> tuple[list[Repository], list[str]]:
    """The repositories context and skills may be read from: each URL https (http to this machine only) without
    credentials in it, each ref a branch, a tag or a commit; and, to fetch them (with `reach`), git on this machine."""
    found: list[Repository] = []
    problems: list[str] = []
    for index, entry in enumerate(entries):
        where = files.location(("repositories", index), data)
        if any(repository.name == entry.name for repository in found):
            problems.append(f"{where}: another repository has this name")
            continue
        url_problem = repositories.url_problem(entry.url)
        if url_problem:
            problems.append(f"{where} → url: {url_problem}")
        if entry.ref is not None and not repositories.REF.match(entry.ref):
            problems.append(f"{where} → ref: {entry.ref!r} is not a branch, a tag or a commit")
        found.append(Repository(name=entry.name, url=entry.url, ref=entry.ref, token_env=entry.token_env,
                                token_user=entry.token_user, refresh_minutes=entry.refresh_minutes))  # fmt: skip
    if entries and not problems and reach:
        git_problem = repositories.git_problem()
        if git_problem:
            problems.append(f"repositories: {git_problem}")
    return found, problems


def _places(
    entries: list[str | InRepository], base_dir: pathlib.Path, by_name: dict[str, Repository], key: str
) -> tuple[tuple[Place, ...], list[str]]:
    """Where context or skills are read from: a file or folder relative to the configuration's folder, which must
    exist, or a path inside a repository the configuration declares — named to it, so only that path is fetched."""
    places: list[Place] = []
    problems: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            path = (base_dir / entry).resolve()
            if not path.exists():
                problems.append(f"{key}: {entry!r} does not exist ({path})")
            places.append(Place(written=entry, local=path))
            continue
        written = f"{entry.path} in {entry.repository}"
        repository = by_name.get(entry.repository)
        path_problem = repositories.path_problem(entry.path)
        if repository is None:
            names = ", ".join(by_name) or "none declared"
            problems.append(f"{key}: no repository named {entry.repository!r} (repositories: {names})")
        elif path_problem:
            problems.append(f"{key}: {entry.path!r} in {entry.repository} {path_problem}")
        else:
            path = repositories.normal(entry.path)
            repository.paths.append(path)
            places.append(Place(written=written, repository=repository, path=path))
    return tuple(places), problems


def _scope_problems(key: str, places: tuple[Place, ...], copies: dict[str, pathlib.Path] | None = None) -> list[str]:
    """What is wrong with a scope's context documents (no two may share a name) or skills (each by the format's
    rules, no two of one name) — with `copies`, as they would be in those copies of its repositories."""
    try:
        paths = repositories.current(places, copies)
        if key == "context":
            documents(paths)
        else:
            skills.skills(paths)
    except (ContextError, skills.SkillError, repositories.RepositoryError) as error:
        return [f"{key}: {error}"]
    return []


def _fetch(
    found: list[Repository], scopes: list[tuple[str, str, tuple[Place, ...]]], reach: bool, every_entry_read: bool
) -> list[str]:
    """Each repository fetched (with `reach`), then each scope's context and skills checked — those in a repository
    only once fetched. Each repository is given the checks of the scopes it serves, for a refresh to pass. A repository
    no entry names is a problem when every entry could be read (one that could not may have been meant to)."""
    problems = []
    for repository in found:
        if not repository.paths:
            if every_entry_read:
                problems.append(f"repositories: {repository.name} is named by no `context` or `skills` entry")
            continue
        if reach:
            try:
                repository.start()
            except repositories.RepositoryError as error:
                problems.append(f"repositories: {repository.described}: {error}")
    for where, key, places in scopes:
        if not places or any(place.repository is not None and place.repository.current is None for place in places):
            continue
        problems.extend(f"{where}{problem}" for problem in _scope_problems(key, places))
    for repository in found:
        served = [(where, key, places) for where, key, places in scopes
                  if any(place.repository is repository for place in places)]  # fmt: skip

        def check(root: pathlib.Path, repository: Repository = repository, served: list = served) -> list[str]:
            copies = {repository.name: root}
            return [f"{where}{one}" for where, key, places in served for one in _scope_problems(key, places, copies)]

        repository.check = check
    return problems


def _vocabularies(entries: list[str], base_dir: pathlib.Path) -> tuple[dict[str, Vocabulary], list[str]]:
    """Vocabulary files (and folders of them) relative to the configuration's folder, by vendor name. A vendor declared
    twice is refused."""
    found: dict[str, Vocabulary] = {}
    problems: list[str] = []
    for path, file in _files(entries, base_dir, "vocabularies", problems):
        read, read_problems = vocabulary.read(path, file)
        problems.extend(f"vocabularies → {problem}" for problem in read_problems)
        if read is None:
            continue
        if read.vendor_name in found:
            problems.append(
                f"vocabularies: {file} declares the vendor {read.vendor_name!r}, as {found[read.vendor_name].file} "
                "does — one vocabulary per vendor"
            )
            continue
        found[read.vendor_name] = read
    return found, problems


def _models(entries: list[str], base_dir: pathlib.Path) -> tuple[list[Model], list[str]]:
    """A connection's semantic models: files and folders relative to the configuration's folder, each checked against
    its version's schema. No two models of a connection may share a name — agents name a model by it."""
    models: list[Model] = []
    problems: list[str] = []
    for path, file in _files(entries, base_dir, "semantic_models", problems):
        read, read_problems = semantic.read(path, file)
        problems.extend(f"semantic_models → {problem}" for problem in read_problems)
        for model in read:
            same = [other.where for other in models if other.name == model.name]
            if same:
                problems.append(f"semantic_models: two models are named {model.name!r} ({same[0]}; {model.where})")
            else:
                models.append(model)
    return models, problems


def _files(entries: list[str], base_dir: pathlib.Path, key: str, problems: list[str]) -> list[tuple[pathlib.Path, str]]:
    """The files of a list of files and folders, each with the name it goes by in a problem (its path as written in
    the configuration, and inside a folder). A missing entry is a problem, and so is a folder with no file to read."""
    found = []
    for entry in entries:
        path = (base_dir / entry).resolve()
        if not path.exists():
            problems.append(f"{key}: {entry!r} does not exist ({path})")
            continue
        inside = semantic.files_of((path,))
        if not inside:
            problems.append(f"{key}: {entry!r} holds no .yaml, .yml or .json file ({path})")
        for file in inside:
            name = entry if file == path else f"{entry.rstrip('/')}/{file.relative_to(path).as_posix()}"
            found.append((file, name))
    return found


def _bind(
    models: list[Model], rules: guard.Rules, adapter: Adapter | None, missing: list[tuple[Model, str, str]] | None
) -> list[str]:
    """Each dataset's source on its connection: a table it may read (and, with `adapter`, one that exists), or a query
    the guard passes. With `missing`, a table the database does not hold is gathered there instead of refused."""
    problems = []
    stored: set[tuple[str, str]] | None = None
    for model in models:
        for index, dataset in enumerate(model.document["datasets"]):
            where = f"{model.where} → {files.location(('datasets', index), model.document)} → source"
            source = dataset["source"]
            if not semantic.is_query(source) and not semantic.is_name(source):
                problems.append(
                    f"{where}: {source[:80]!r} is neither a table name (schema.table) nor a query starting with SELECT "
                    "or WITH"
                )
                continue
            try:
                if semantic.is_query(source):
                    guard.check(source, rules)
                    continue
                schema, table = guard.resolve_table(source, rules)
            except guard.Refused as refusal:
                problems.append(f"{where}: {refusal.message} ({refusal.code})")
                continue
            if adapter is None:
                continue
            if stored is None:
                try:
                    stored = {
                        (guard.stored_name(t.schema, rules), guard.stored_name(t.name, rules)) for t in adapter.tables()
                    }
                except AdapterError as error:
                    return [f"the tables of the connection cannot be listed: {error}"]
            if (schema, table) in stored:
                continue
            if missing is None:
                problems.append(f"{where}: there is no table or view {schema}.{table} on the connection")
            else:
                missing.append((model, dataset["name"], f"{schema}.{table}"))
    return problems


def _left_out(missing: list[tuple[Model, str, str]]) -> tuple[str, ...]:
    """Each dataset whose table the database does not hold taken out of its model, with the relationships to or from
    it — so no answer names a table that is not there. Its name and table, for the check to say."""
    for model in {id(model): model for model, _, _ in missing}.values():
        gone = {name for owner, name, _ in missing if owner is model}
        document = model.document
        document["datasets"] = [dataset for dataset in document["datasets"] if dataset["name"] not in gone]
        if "relationships" in document:
            kept = [r for r in document["relationships"] if r["from"] not in gone and r["to"] not in gone]
            document["relationships"] = kept
    return tuple(f"{model.name}.{name} ({table})" for model, name, table in missing)


def _own_metadata(models: list[Model], vocabularies: dict[str, Vocabulary]) -> list[str]:
    """Each custom extension of a vendor with a vocabulary, checked against it; others pass as written — but not one
    whose vendor name differs from a declared one only in case or spaces, which would pass unchecked by a slip."""
    problems = []
    loosely = {vendor.casefold().strip(): vendor for vendor in vocabularies}
    for model in models:
        for place, level, extension in semantic.extensions(model.document):
            vendor = extension["vendor_name"]
            declared = vocabularies.get(vendor)
            if declared is None:
                meant = loosely.get(vendor.casefold().strip())
                if meant is not None:
                    where = f"{model.where} → {files.location(place, model.document)} → vendor_name"
                    problems.append(
                        f"{where}: {vendor!r} is not {meant!r}, the vendor a vocabulary declares — write it exactly, "
                        "or its data passes unchecked"
                    )
                continue
            where = f"{model.where} → {files.location(place, model.document)} ({declared.vendor_name})"
            data = extension["data"]
            if isinstance(data, str):
                problems.append(f"{where}: the data is not JSON, so {declared.file} cannot check it")
                continue
            problems.extend(f"{where} → {problem}" for problem in declared.check(data, level))
    return problems


def _check_tables(entry: ConnectionEntry) -> list[str]:
    problems = []
    for pattern in entry.readable:
        if not TWO_PARTS.match(pattern):
            problems.append(f"readable: {pattern!r} must be schema.table (globs allowed: marts.*, *.*)")
    for large in entry.large_tables:
        if not TWO_PARTS.match(large.table):
            problems.append(f"large_tables: {large.table!r} must be schema.table")
            continue
        schema, table = large.table.split(".")
        if not any(
            fnmatchcase(schema, p.split(".")[0]) and fnmatchcase(table, p.split(".")[1])
            for p in entry.readable
            if TWO_PARTS.match(p)
        ):
            problems.append(f"large_tables: {large.table!r} is not covered by any readable pattern")
    return problems
