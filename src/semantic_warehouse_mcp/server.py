"""The MCP server. Every tool is read-only and says so (dev/DESIGN.md, "The guard")."""

from __future__ import annotations

import argparse
import copy
import datetime
import difflib
import os
import signal
import sys
import time
from collections import Counter
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, answers, context, guard, repositories, semantic, serving, skills
from .adapters import AdapterError, TimedOut
from .config import Config, ConfigError, Connection, load
from .semantic import Model

# Every tool of this server carries these annotations; a test holds it.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

CONFIG_VARIABLE = "SEMANTIC_WAREHOUSE_MCP_CONFIG"

# `describe_dataset` shortens each own metadata value longer than this many characters, unless asked for it in full.
SHORT_METADATA = 1000
# `describe_dataset`, asked for its own metadata in full, cuts its texts to this many characters before it leaves out
# any field.
LONG_TEXT = 2000
# `list_metadata` lists each value of a key with at most this many values, none longer than this many characters.
MENU_VALUES = 50
MENU_TEXT = 100
LOG_NAMES = 5  # the datasets left out the start's log names; `--check` names every one

# `run_query`'s description, when the configuration asks it to carry the server's instructions, leads them in with this.
CARRIED = (
    "The instructions this server gives a client when it connects follow — here too, because some clients and "
    "gateways do not pass them on. Read them before the first query."
)


def own_instructions(metrics: bool) -> str:
    """The server's own instructions. The metric tools are named only where a model defines a metric: elsewhere they
    are not offered."""
    return (
        "semantic-warehouse-mcp answers questions about data in SQL databases, and never changes them. "
        "Start with `list_connections`. Where a connection has a semantic model, find the data in it first: "
        "`list_datasets` and `describe_dataset` (what each table means, its fields, keys and relationships, the team's "
        "own metadata with what its values mean)"
        + (
            ", `list_metrics` and `describe_metric` (how a number is defined) — use a metric's definition rather than "
            "writing your own"
            if metrics
            else ""
        )
        + ". `list_metadata` shows the values of the team's own metadata (its categories, layers, …), which "
        + ("`list_datasets` and `list_metrics` filter by" if metrics else "`list_datasets` filters by")
        + ". `list_tables` and `describe_table` show every readable table as the database holds it. `run_query` runs "
        "one read-only SELECT. Each query is checked first: a refusal says why and how to fix it. A large table must "
        "be read alone in its SELECT (joined as a subquery of its own), bounded by its column, and some held to named "
        "values of another (shown in `list_connections`, `describe_dataset` and `describe_table`). "
        "The team that runs this server may give instructions (below, and in `list_connections`), context documents "
        "(`list_context`, `get_context`) and skills — how it does a kind of task (`list_skills`, `get_skill`): read "
        "the ones that bear on the question before querying. "
        "Every answer carries the SQL as it ran and the time — cite them."
    )


def defines_metrics(config: Config | None) -> bool:
    """Whether any semantic model of the configuration defines a metric — its own or one of a dataset's."""
    return any(
        model.document.get("metrics") or any(dataset.get("metrics") for dataset in model.document["datasets"])
        for connection in (config.connections.values() if config else ())
        for model in connection.models
    )


def instructions(config: Config | None) -> str:
    """The server's instructions, and the team's own after them, as written: for every connection, then for each."""
    parts = [own_instructions(defines_metrics(config))]
    if config and config.instructions:
        parts.append(f"Instructions for every connection:\n{config.instructions.strip()}")
    for connection in config.connections.values() if config else ():
        if connection.instructions:
            parts.append(f"Instructions for the connection `{connection.name}`:\n{connection.instructions.strip()}")
    return "\n\n".join(parts)


def shorten_metadata(extensions: list, limit: int) -> tuple[list, bool]:
    """Own metadata with each value longer than `limit` characters made short — a text longer than `limit` characters
    to its first `limit`, ending in `…`; a list or a mapping longer than `limit` characters as JSON to a note of that
    length — and whether any was. A vendor's data that is no mapping is one value."""

    def short(value: Any) -> Any:
        if isinstance(value, str):
            return value if len(value) <= limit else value[:limit] + "…"
        size = len(answers.dumps(value))
        return value if size <= limit else f"… ({size} characters as JSON)"

    made = []
    for extension in extensions:
        data = extension.get("data")
        if isinstance(data, dict):
            made.append({**extension, "data": {key: short(value) for key, value in data.items()}})
        else:
            made.append({**extension, "data": short(data)} if "data" in extension else extension)
    return made, made != extensions


def build_server(config: Config | None = None) -> MCPServer:
    """The server with its tools — built fresh, so tests and transports each get their own."""
    said = instructions(config)
    server = MCPServer(name="semantic-warehouse-mcp", version=__version__, instructions=said)
    connections = config.connections if config else {}
    server_context = config.context if config else ()
    server_skills = config.skills if config else ()
    vocabularies = config.vocabularies if config else {}
    answer_characters = config.limits.answer_characters if config else 40_000
    connection_rules = {name: connection.rules for name, connection in connections.items()}

    def pick(name: str | None) -> Connection:
        names = ", ".join(connections) or "none configured"
        if name is None:
            if len(connections) == 1:
                return next(iter(connections.values()))
            raise ToolError(f"Name the connection: {names}.")
        if name not in connections:
            raise ToolError(f"No connection named {name!r}. Connections: {names}.")
        return connections[name]

    @server.tool(annotations=READ_ONLY)
    def about() -> dict:
        """What this server is, its version and status. Call it to check the connection."""
        return {
            "name": "semantic-warehouse-mcp",
            "version": __version__,
            "status": (
                "pre-alpha: tables, read-only queries, semantic models in Apache Ossie, context and skills; metrics "
                "are being built"
            ),
            "read_only": True,
        }

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_connections() -> str:
        """The databases this server reads. For each connection: its name, engine and description; the team's
        instructions for it, as written; which tables are readable (schema.table patterns); which large tables a query
        must bound by which column (`bound_by`) and hold to named values of which (`narrowed_by`); the limits on rows
        and seconds per query; the names of its semantic models (their datasets in `list_datasets`), of its context
        documents (read them with `get_context`) and of its skills (read them with `get_skill`)."""
        items = []
        for connection in connections.values():
            item = {"name": connection.name, "engine": connection.engine}
            if connection.description:
                item["description"] = connection.description
            if connection.instructions:
                item["instructions"] = connection.instructions
            item["readable"] = list(connection.readable)
            if connection.large_tables:
                item["large_tables"] = [
                    {"table": large.table, **large_facts(large)} for large in connection.large_tables
                ]
            item["limits"] = {"rows": connection.limits.rows, "seconds": connection.limits.seconds}
            if connection.models:
                item["semantic_models"] = [model.name for model in connection.models]
            if connection.context:
                item["context"] = list(context_documents(connection.name))
            if connection.skills:
                item["skills"] = list(skills_of(connection.name))
            items.append(item)
        answer = {}
        if config and config.instructions:
            answer["instructions"] = config.instructions
        if server_context:
            answer["context"] = list(context_documents(None))
        if server_skills:
            answer["skills"] = list(skills_of(None))
        if config and config.repositories:
            answer["repositories"] = [repository_item(repository) for repository in config.repositories.values()]
        return answers.render(answer, "connections", items, answer_characters, "")

    def repository_item(repository: repositories.Repository) -> dict:
        item: dict = {"name": repository.name, "url": repository.url}
        if repository.ref:
            item["ref"] = repository.ref
        if repository.current is not None:
            item["commit"] = repository.current.commit
            item["fetched_at"] = repository.current.fetched_at.isoformat(timespec="seconds")
        if repository.checked_at is not None:
            item["checked_at"] = repository.checked_at.isoformat(timespec="seconds")
        item["refresh_minutes"] = repository.refresh_minutes
        if repository.problem:
            item["problem"] = repository.problem
        return item

    def provenance(places: tuple[repositories.Place, ...], path) -> dict:
        """Where a served file comes from, when it is a repository's: its name and the commit."""
        for place in places:
            copy = place.repository.copy_of(path) if place.repository is not None else None
            if copy is not None:
                return {"fetched_from": place.repository.name, "commit": copy.commit}
        return {}

    def context_places(connection: str | None) -> tuple[repositories.Place, ...]:
        return server_context if connection is None else connections[connection].context

    def context_documents(connection: str | None) -> dict:
        """A scope's documents by name — the whole server's (None) or a connection's — read anew from disk."""
        try:
            return context.documents(repositories.current(context_places(connection)))
        except context.ContextError as error:
            raise ToolError(f"The context documents cannot be read: {error}") from error

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_context(connection: str | None = None) -> str:
        """The context documents the team wrote for agents — rules, definitions, how its data is made — with each
        document's title and length. Those for the whole server have no `connection`; give `connection` to list only
        the whole server's and that connection's."""
        if connection is not None:
            pick(connection)
        items = []
        for scope in [None, *connections]:
            if connection is not None and scope not in (None, connection):
                continue
            for name, path in context_documents(scope).items():
                text = path.read_text(encoding="utf-8", errors="replace")
                item = {"name": name, "title": context.title(text), "characters": len(text)}
                if scope is not None:
                    item["connection"] = scope
                items.append(item)
        return answers.render({}, "documents", items, answer_characters, "Give `connection` to list fewer.")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def get_context(name: str, connection: str | None = None, offset: int = 0) -> str:
        """One context document, as the team wrote it. `name` as `list_context` shows it; `connection` for a
        connection's document (left out, the whole server's — or the only document of that name). A long document
        comes in parts: call again with `offset` set to the answer's `next_offset`."""
        scopes = [connection] if connection is not None else [None, *connections]
        if connection is not None:
            pick(connection)
        listed = {scope: context_documents(scope) for scope in scopes}  # once each: a refresh may swap a copy between
        found = [(scope, listed[scope][name]) for scope in scopes if name in listed[scope]]
        if not found:
            raise ToolError(f"No context document named {name!r}. list_context shows them.")
        if len(found) > 1 and found[0][0] is not None:
            owners = ", ".join(scope for scope, _ in found)
            raise ToolError(f"{name!r} is a document of more than one connection ({owners}): give `connection`.")
        scope, path = found[0]
        text = path.read_text(encoding="utf-8", errors="replace")
        if not 0 <= offset <= len(text):
            raise ToolError(f"`offset` must be between 0 and {len(text)}, the document's length.")
        answer: dict = {"name": name}
        if scope is not None:
            answer["connection"] = scope
        answer |= provenance(context_places(scope), path)
        return answers.in_parts(answer, text, offset, answer_characters)

    # --- skills -------------------------------------------------------------------------------------------------------

    def skill_places(connection: str | None) -> tuple[repositories.Place, ...]:
        return server_skills if connection is None else connections[connection].skills

    def skills_of(connection: str | None) -> dict[str, skills.Skill]:
        """A scope's skills by name — the whole server's (None) or a connection's — read anew from disk."""
        try:
            return skills.skills(repositories.current(skill_places(connection)))
        except skills.SkillError as error:
            raise ToolError(f"The skills cannot be read: {error}") from error

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_skills(connection: str | None = None) -> str:
        """The skills the team wrote for agents — each how it does a kind of task (answers a kind of question, checks
        a number) — with its description, which says when to use it. Read one that fits the task with `get_skill`
        before starting. Those for the whole server have no `connection`; give `connection` to list only the whole
        server's and that connection's."""
        if connection is not None:
            pick(connection)
        items = []
        for scope in [None, *connections]:
            if connection is not None and scope not in (None, connection):
                continue
            for skill in skills_of(scope).values():
                item = {"name": skill.name, "description": skill.description}
                if scope is not None:
                    item["connection"] = scope
                items.append(item)
        return answers.render({}, "skills", items, answer_characters, "Give `connection` to list fewer.")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def get_skill(name: str, connection: str | None = None, file: str | None = None, offset: int = 0) -> str:
        """One skill, as the team wrote it: its SKILL.md, with the list of the other files in its folder. A file the
        SKILL.md refers to (`references/rules.md`) is read with `file` set to its name as the list shows it. `name` as
        `list_skills` shows it; `connection` for a connection's skill (left out, the whole server's — or the only
        skill of that name). A long file comes in parts: call again with `offset` set to the answer's `next_offset`."""
        scopes = [connection] if connection is not None else [None, *connections]
        if connection is not None:
            pick(connection)
        listed = {scope: skills_of(scope) for scope in scopes}  # once each: a refresh may swap a copy between
        found = [(scope, listed[scope][name]) for scope in scopes if name in listed[scope]]
        if not found:
            raise ToolError(f"No skill named {name!r}. list_skills shows them.")
        if len(found) > 1 and found[0][0] is not None:
            owners = ", ".join(scope for scope, _ in found)
            raise ToolError(f"{name!r} is a skill of more than one connection ({owners}): give `connection`.")
        scope, skill = found[0]
        try:
            inside, not_text = skills.files_of(skill)
        except OSError as error:
            raise ToolError(f"The files of the skill {name!r} cannot be read: {error.strerror or error}") from error
        if file in (None, skills.SKILL_FILE):
            path = skill.file
        elif file in inside:
            path = inside[file]
        else:
            raise ToolError(f"The skill {name!r} has no file {file!r}. get_skill without `file` lists its files.")
        try:
            text = skills.text_of(path, f"{name}/{path.relative_to(skill.folder).as_posix()}")
        except skills.SkillError as error:
            raise ToolError(f"The skill cannot be read: {error}") from error
        if not 0 <= offset <= len(text):
            raise ToolError(f"`offset` must be between 0 and {len(text)}, the file's length.")
        answer: dict = {"name": skill.name}
        if scope is not None:
            answer["connection"] = scope
        answer["file"] = path.relative_to(skill.folder).as_posix()
        answer |= provenance(skill_places(scope), path)
        if path == skill.file:
            shown = {"files": list(inside), "not_text": not_text}
            while len(answers.dumps({**answer, **shown})) > answer_characters // 2 and any(shown.values()):
                longer = max(shown, key=lambda key: len(shown[key]))  # the text keeps half: the longer list is cut
                shown[longer] = shown[longer][: len(shown[longer]) * 9 // 10]
            answer["files"] = shown["files"]
            if not_text:
                answer["not_text"] = shown["not_text"]
            if (len(shown["files"]), len(shown["not_text"])) != (len(inside), len(not_text)):
                also = f" and {len(shown['not_text'])} of its {len(not_text)} not text" if not_text else ""
                answer["note"] = (
                    f"Only the first {len(shown['files'])} of the skill's {len(inside)} files{also} fit in the answer "
                    f"({answer_characters} characters)."
                )
        return answers.in_parts(answer, text, offset, answer_characters)

    # --- the semantic models ------------------------------------------------------------------------------------------

    def models_of(connection: str | None, model: str | None) -> list[tuple[Connection, Model]]:
        """The models a semantic tool looks in: of the connection named, or of every one; of the model named, or all."""
        scope = [pick(connection)] if connection is not None else list(connections.values())
        found = [(c, m) for c in scope for m in c.models if model is None or m.name == model]
        if model is not None and not found:
            names = ", ".join(sorted({m.name for c in scope for m in c.models})) or "none"
            raise ToolError(f"No semantic model named {model!r}. Models: {names}.")
        return found

    def one(found: list[tuple], kind: str, name: str, lister: str) -> tuple:
        """The one (connection, model, dataset or None, node) found by a name, or an error naming every place."""
        if not found:
            raise ToolError(f"No {kind} named {name!r}. {lister} shows them.")
        if len(found) > 1:
            places = "; ".join(
                f"model {m.name} on {c.name}" + (f", dataset {home['name']}" if home is not None else "")
                for c, m, home, _ in found
            )
            raise ToolError(f"{name!r} is a {kind} of more than one place ({places}): name the one you mean.")
        return found[0]

    def definitions(nodes: list[dict]) -> dict:
        """What the vocabularies say of the own metadata these parts of a model use, by vendor: each key's description,
        and the definitions of the values used."""
        found: dict = {}
        for node in nodes:
            for extension in node.get("custom_extensions", []):
                declared = vocabularies.get(extension["vendor_name"])
                if declared is None:
                    continue
                for key, said in declared.definitions(extension["data"]).items():
                    slot = found.setdefault(declared.vendor_name, {}).setdefault(key, {})
                    if "description" in said:
                        slot["description"] = said["description"]
                    if "values" in said:
                        slot.setdefault("values", {}).update(said["values"])
        return found

    def summary(chosen: Connection, model: Model, node: dict, kind: str, **extra) -> dict:
        """A dataset or a metric for a list: where it is, its name, the first line of its description, its synonyms,
        and the own metadata its vocabulary shows in lists (`in_lists`), as written."""
        item = {"connection": chosen.name, "model": model.name, kind: node["name"], **extra}
        description = semantic.first_line(node.get("description"))
        if description:
            item["description"] = description
        ai_context = node.get("ai_context")
        if isinstance(ai_context, dict) and ai_context.get("synonyms"):
            item["synonyms"] = ai_context["synonyms"]
        shown = []
        for extension in node.get("custom_extensions", []):
            declared = vocabularies.get(extension["vendor_name"])
            data = extension["data"]
            if declared is None or not isinstance(data, dict):
                continue
            kept = {key: value for key, value in data.items() if key in declared.keys and declared.keys[key].in_lists}
            if kept:
                shown.append({"vendor_name": extension["vendor_name"], "data": kept})
        if shown:
            item["custom_extensions"] = shown
        return item

    def listing(key: str, kind: str, items: list[dict], searched: bool, describer: str, notes: list[str]) -> str:
        """A list of datasets or metrics: in full when it fits; else every one by name only, grouped by its model —
        the map of what there is, in one answer — and, past that too, the first names that fit. `notes` say what of
        the filter asked for matches nothing the models hold."""
        answer: dict = {}
        if not any(c.models for c in connections.values()):
            notes = ["No connection has a semantic model; list_tables and describe_table show the tables.", *notes]
        if notes:
            answer["note"] = " ".join(notes)
        said = definitions(items)
        full = {**answer, **({"vocabulary": said} if said else {}), key: items}
        if len(answers.dumps(full)) <= answer_characters:
            return answers.dumps(full)
        groups: dict[tuple[str, str], list[str]] = {}
        for item in items:
            name = item[kind] if "dataset" not in item or kind == "dataset" else f"{item['dataset']}.{item[kind]}"
            groups.setdefault((item["connection"], item["model"]), []).append(name)
        named = [{"connection": c, "model": m, "names": names} for (c, m), names in groups.items()]
        order = ", the closest to `search` first" if searched else ""
        compact = {
            "compact": True,
            "note": f"The full list of {len(items)} {key} does not fit in the answer ({answer_characters} characters): "
            f"each is named only{order}. Give `search` or `metadata` for their descriptions, or {describer} for one.",
            key: named,
        }
        shown = len(items)
        while len(answers.dumps(compact)) > answer_characters and shown:
            longest = max(named, key=lambda group: len(group["names"]))
            shown -= len(longest["names"]) - len(longest["names"]) * 9 // 10
            longest["names"] = longest["names"][: len(longest["names"]) * 9 // 10]
            compact["truncated"] = True
            compact["note"] = (
                f"The full list of {len(items)} {key} does not fit in the answer ({answer_characters} characters), "
                f"nor do all their names: only {shown} are named{order}. Give `search`, `metadata` or `connection` to "
                "list fewer."
            )
        if notes:
            compact["note"] = f"{' '.join(notes)} {compact['note']}"
        return answers.dumps(compact)

    def wanted_metadata(metadata: dict[str, Any] | None) -> dict[str, list[Any]]:
        """`metadata` as a list tool takes it — each key with a value or a list of them — as lists of values."""
        wanted = {}
        for key, value in (metadata or {}).items():
            values = value if isinstance(value, list) else [value]
            if not values:
                raise ToolError(f"`metadata` → {key}: give a value, or a list of values any one of which will do.")
            wanted[key] = values
        return wanted

    def metadata_notes(wanted: dict[str, list[Any]], scope: list[tuple[Connection, Model]]) -> list[str]:
        """What of a metadata filter nothing can match: a key no vocabulary declares and no model in scope holds, a
        value not among the ones a vocabulary closes the key to — each with the likely one meant."""
        notes = []
        held_keys = {
            key
            for _, model in scope
            for _, _, extension in semantic.extensions(model.document)
            if isinstance(extension.get("data"), dict)
            for key in extension["data"]
        }
        declared = {key: entry for vocabulary in vocabularies.values() for key, entry in vocabulary.keys.items()}
        for key, values in wanted.items():
            if key not in declared and key not in held_keys:
                known = sorted(set(declared) | held_keys)
                hint = difflib.get_close_matches(key, known, n=1)
                notes.append(
                    f"No own metadata here has the key {key!r}"
                    + (f" — did you mean {hint[0]!r}?" if hint else ".")
                    + " list_metadata shows the keys and their values."
                )
                continue
            closed = declared[key].values if key in declared else None
            if not closed:
                continue
            for value in values:
                if value not in closed:
                    hint = difflib.get_close_matches(str(value), list(closed), n=1)
                    notes.append(
                        f"{value!r} is not one of the values of {key!r}"
                        + (f" — did you mean {hint[0]!r}?" if hint else ".")
                        + " list_metadata shows them."
                    )
        return notes

    def matched(node: dict, rank: tuple[int, int], words: list[str], wanted: dict[str, list[Any]]) -> list[str] | None:
        """The fields of a dataset where a list found what it was asked for — the own metadata `wanted` the dataset
        does not hold itself, the words of a search it does not say of itself — or None when it does not match."""
        named = semantic.metadata_match(node, wanted) if wanted else []
        if named is None:
            return None
        if rank[0] == 2:
            named = [*named, *semantic.fields_holding(node, words)]
        return [field["name"] for field in node.get("fields", []) if field["name"] in named]

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_datasets(
        connection: str | None = None,
        search: str | None = None,
        metadata: dict[str, str | int | float | bool | list[str | int | float | bool]] | None = None,
    ) -> str:
        """The datasets of the semantic models: the tables the team described for agents. For each: its connection
        and model, its name, its `source` table, the first line of its description (`…` when there is more), its
        synonyms, and the team's own metadata shown in lists — with what each value means in `vocabulary`. When they
        do not all fit, every dataset is named only, by model (`compact`): the map of what there is. `search` keeps the
        datasets holding every one of its words, in any case, anywhere in the dataset as written (name, description,
        fields, own metadata) — those with the words in their name first, then in their description, then elsewhere.
        `metadata` keeps the datasets whose own metadata holds, for each key given, its value or one of a list of
        values (`{"tags": "sales", "layer": ["mart", "dim"]}`; `list_metadata` shows the keys and values) — the
        dataset itself, or one of its fields. `matched_fields` names the fields where a dataset matched when it did
        not itself. Read one in full with `describe_dataset`."""
        words = search.split() if search else []
        wanted = wanted_metadata(metadata)
        scope = models_of(connection, None)
        found = []
        places = [(chosen, owner, dataset) for chosen, owner in scope for dataset in owner.document["datasets"]]
        for index, (chosen, owner, dataset) in enumerate(places):
            rank = semantic.rank(dataset, words)
            named = matched(dataset, rank, words, wanted) if rank is not None else None
            if named is None:
                continue
            item = summary(chosen, owner, dataset, "dataset", source=dataset["source"])
            if named:
                item["matched_fields"] = named
            found.append((rank, index, item))
        items = [item for _, _, item in sorted(found, key=lambda entry: entry[:2])]
        notes = metadata_notes(wanted, scope)
        return listing("datasets", "dataset", items, bool(words), "describe_dataset", notes)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def describe_dataset(
        dataset: str, connection: str | None = None, model: str | None = None, full: bool = False
    ) -> str:
        """One dataset of a semantic model in full, as the team wrote it: its source, keys, description,
        `ai_context`, its own metadata (`custom_extensions`); its fields, each with its expression — and the live
        column's type and comment when a field is one column; its own metrics; its relationships. Beside them: the
        source table as the database holds it now (`table`, with `bound_by` and `narrowed_by` when a query must bound
        or narrow it), its columns no field describes, and what the vocabulary says each own metadata value means.
        A field whose expression is nothing but its own name comes without `expression`: it is the column of that
        name. An own metadata value longer than 1000 characters (a table's SQL, a long list) is shortened — a text to
        its beginning, ending in `…`, a list or a mapping to a note of its length — and `note` says so: `full: true`
        serves them whole. Give `connection` or `model` when two models have a dataset of that name."""
        found = [
            (chosen, owner, None, node)
            for chosen, owner in models_of(connection, model)
            for node in owner.document["datasets"]
            if node["name"] == dataset
        ]
        chosen, owner, _, node = one(found, "dataset", dataset, "list_datasets")
        answer: dict = {"connection": chosen.name, "model": owner.name, "dataset": node["name"]}
        answer |= {key: value for key, value in node.items() if key not in ("name", "fields", "metrics")}
        shortened = False
        if not full and "custom_extensions" in answer:
            answer["custom_extensions"], shortened = shorten_metadata(answer["custom_extensions"], SHORT_METADATA)
        columns: list = []
        if semantic.is_query(node["source"]):
            answer["table"] = {"note": "the source is a query, not a table"}
        else:
            try:
                schema, name = guard.resolve_table(node["source"], chosen.rules)
                answer["table"], columns = live_table(chosen, schema, name)
                if not columns:
                    answer["table"]["note"] = "there is no such table on the connection now"
            except (guard.Refused, AdapterError) as error:
                answer["table"] = {"note": f"the source table cannot be read: {error}"}
        by_name = {guard.stored_name(column.name, chosen.rules): column for column in columns}
        fields, described = [], set()
        for field in node.get("fields", []):
            itself = semantic.names_itself(field)  # its expression is its name: the column of that name, said once
            item = {key: value for key, value in field.items() if not (key == "expression" and itself)}
            if not full and "custom_extensions" in item:
                item["custom_extensions"], cut = shorten_metadata(item["custom_extensions"], SHORT_METADATA)
                shortened = shortened or cut
            name = semantic.bare_column(field)
            column = by_name.get(guard.stored_name(name, chosen.rules)) if name else None
            if column is not None:
                described.add(column.name)
                item["column"] = column_item(column, name=False)
            fields.append(item)
        if node.get("metrics"):
            answer["metrics"] = node["metrics"]
        relationships = [r for r in owner.document.get("relationships", []) if node["name"] in (r["from"], r["to"])]
        if relationships:
            answer["relationships"] = relationships
        other = [column_item(column) for column in columns if column.name not in described]
        if other:
            answer["other_columns"] = other
        said = definitions([node, *node.get("fields", []), *node.get("metrics", []), *relationships])
        if said:
            answer["vocabulary"] = said
        if shortened:
            answer["note"] = (
                f"Own metadata values longer than {SHORT_METADATA} characters are shortened — a text to its "
                "beginning, ending in …, a list or a mapping to a note of its length: `full: true` serves them whole."
            )
        own = answer.get("custom_extensions")
        cut = cut_texts(own, LONG_TEXT)
        smaller = [
            ("other_columns", [column["name"] for column in other], "`other_columns` holds names only"),
            *(
                [
                    (
                        "custom_extensions",
                        cut,
                        f"own metadata texts longer than {LONG_TEXT} characters cut, each ending in …",
                    )
                ]
                if cut != own
                else []
            ),
            ("other_columns", None, "`other_columns` left out (describe_table shows them)"),
            ("relationships", None, "`relationships` left out"),
            ("metrics", None, "`metrics` left out (describe_metric shows them)"),
            ("vocabulary", None, "`vocabulary` left out"),
        ]
        return answers.render_fitting(answer, "fields", fields, answer_characters, "", smaller)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_metrics(
        connection: str | None = None,
        search: str | None = None,
        metadata: dict[str, str | int | float | bool | list[str | int | float | bool]] | None = None,
    ) -> str:
        """The metrics of the semantic models: how the team defines its numbers. For each: its connection and model,
        its name, its `dataset` when it is a dataset's own metric, the first line of its description (`…` when there
        is more), its synonyms, and the team's own metadata shown in lists — with what each value means in
        `vocabulary`. `search` keeps the metrics holding every one of its words, in any case, anywhere in the metric as
        written — those with the words in their name first, then in their description, then elsewhere. `metadata`
        keeps the metrics whose own metadata holds, for each key given, its value or one of a list of values
        (`list_metadata` shows them). When they do not all fit, every metric is named only, by model (`compact`). Read
        one in full — its expression — with `describe_metric`."""
        words = search.split() if search else []
        wanted = wanted_metadata(metadata)
        scope = models_of(connection, None)
        found = []
        for chosen, model in scope:
            places = [(None, metric) for metric in model.document.get("metrics", [])]
            places += [(d, metric) for d in model.document["datasets"] for metric in d.get("metrics", [])]
            for dataset, metric in places:
                rank = semantic.rank(metric, words)
                if rank is None or (wanted and semantic.metadata_match(metric, wanted) is None):
                    continue
                extra = {"dataset": dataset["name"]} if dataset is not None else {}
                found.append((rank, len(found), summary(chosen, model, metric, "metric", **extra)))
        items = [item for _, _, item in sorted(found, key=lambda e: e[:2])]
        notes = metadata_notes(wanted, scope)
        return listing("metrics", "metric", items, bool(words), "describe_metric", notes)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_metadata(connection: str | None = None) -> str:
        """The team's own metadata the semantic models hold — what `list_datasets` (and `list_metrics`, where a model
        defines metrics) filter by (`metadata`). For each vendor and key: what the vocabulary says it is, and on how
        many datasets, fields and metrics it is. For a key of short values — words, numbers, a list of them — each
        value: on how many it is, and what the vocabulary says it means, the most used first; a key of more than 50
        values, or of long texts, says how many it has."""
        found: dict[tuple[str, str], dict] = {}
        for _, model in models_of(connection, None):
            for _, level, extension in semantic.extensions(model.document):
                data = extension.get("data")
                if not isinstance(data, dict):
                    continue
                for key, value in data.items():
                    slot = found.setdefault((extension["vendor_name"], key), {"levels": Counter(), "values": {}})
                    slot["levels"][level] += 1
                    for item in value if isinstance(value, list) else [value]:
                        if isinstance(item, (str, int, float, bool)):
                            counted = slot["values"].setdefault((type(item).__name__, item), [item, Counter()])
                            counted[1][level] += 1
                        else:
                            slot["opaque"] = True
        items = []
        for (vendor, key), slot in found.items():
            declared = vocabularies.get(vendor)
            entry = declared.keys.get(key) if declared else None
            item: dict = {"vendor_name": vendor, "key": key}
            if entry is not None and entry.description:
                item["description"] = entry.description
            item |= {f"{level}s": count for level, count in slot["levels"].items()}
            values = sorted(slot["values"].values(), key=lambda v: (-sum(v[1].values()), str(v[0])))
            closed = entry.values if entry is not None and entry.values else None
            short = all(len(str(value)) <= MENU_TEXT for value, _ in values)
            if values and not slot.get("opaque") and (closed or (len(values) <= MENU_VALUES and short)):
                item["values"] = []
                for value, counts in values:
                    shown = {"value": value, **{f"{level}s": count for level, count in counts.items()}}
                    if isinstance(closed, dict) and isinstance(value, str) and closed.get(value):
                        shown["definition"] = closed[value]
                    item["values"].append(shown)
            elif values:
                item["distinct_values"] = len(values)
            items.append(item)
        order = {(vendor, key): index for vendor, v in vocabularies.items() for index, key in enumerate(v.keys)}
        items.sort(key=lambda item: (item["vendor_name"], order.get((item["vendor_name"], item["key"]), len(order))))
        hint = "Give `connection` to list fewer."
        return answers.render({}, "metadata", items, answer_characters, hint)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def describe_metric(
        metric: str, connection: str | None = None, model: str | None = None, dataset: str | None = None
    ) -> str:
        """One metric in full, as the team wrote it: its expression in each SQL dialect written, description, data
        type, `ai_context` and own metadata (`custom_extensions`) — with what the vocabulary says each own metadata
        value means, and the datasets its expression reads: each one's source table, and the fields the expression
        names with their own expressions (a field may be a calculation over the table's columns, not a column).
        `dataset` names a dataset's own metric; give `connection` or `model` when two models define a metric of that
        name."""
        found = []
        for chosen, owner in models_of(connection, model):
            if dataset is None:
                found.extend((chosen, owner, None, m) for m in owner.document.get("metrics", []) if m["name"] == metric)
            for home in owner.document["datasets"]:
                if dataset is None or home["name"] == dataset:
                    found.extend((chosen, owner, home, m) for m in home.get("metrics", []) if m["name"] == metric)
        chosen, owner, home, node = one(found, "metric", metric, "list_metrics")
        answer: dict = {"connection": chosen.name, "model": owner.name, "metric": node["name"]}
        if home is not None:
            answer["dataset"] = home["name"]
        answer |= {key: value for key, value in node.items() if key != "name"}
        said = definitions([node])
        if said:
            answer["vocabulary"] = said
        sources = {d["name"]: d["source"] for d in owner.document["datasets"]}
        read = semantic.fields_read(node, owner.document, home)
        if home is not None:
            read.setdefault(home["name"], [])
        items = [
            {
                "dataset": name,
                "source": sources[name],
                "fields": [{"name": f["name"], "expression": f["expression"]} for f in fields],
            }
            for name, fields in read.items()
        ]
        return answers.render(answer, "datasets", items, answer_characters, "")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_tables(connection: str | None = None) -> str:
        """The readable tables and views of a connection, with their comments as the database holds them. A large
        table shows `bound_by`: the column a query over it must bound from below — and `narrowed_by`, the columns it
        must hold to values it names (`= …`, `IN (…)`). `connection` may be left out when there is only one."""
        chosen = pick(connection)
        connection_rule = connection_rules[chosen.name]
        try:
            tables = chosen.adapter.tables()
        except AdapterError as error:
            raise ToolError(f"The database ({chosen.name}) returned an error: {error}") from error
        items = []
        for table in tables:
            if not guard.is_readable(table.schema, table.name, connection_rule):
                continue
            item = {"table": f"{table.schema}.{table.name}", "kind": table.kind}
            if table.comment:
                item["comment"] = table.comment
            item |= large_facts(guard.large_table(table.schema, table.name, connection_rule))
            items.append(item)
        return answers.render(
            {"connection": chosen.name}, "tables", items, answer_characters, "Name a table in describe_table."
        )

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def describe_table(table: str, connection: str | None = None) -> str:
        """A readable table's columns in order — name, type, whether it can be null, and the column's comment as the
        database holds it — with the table's own comment, and `bound_by` (and `narrowed_by`) when it is a large table.
        `table` is
        `schema.table`, or a table of the default schema."""
        chosen = pick(connection)
        try:
            schema, name = guard.resolve_table(table, connection_rules[chosen.name])
        except guard.Refused as refusal:
            raise ToolError(f"Refused ({refusal.code}): {refusal.message}.") from refusal
        try:
            facts, columns = live_table(chosen, schema, name)
        except AdapterError as error:
            raise ToolError(f"The database ({chosen.name}) returned an error: {error}") from error
        if not columns:
            raise ToolError(f"No table {schema}.{name} on {chosen.name}. list_tables shows the readable ones.")
        answer: dict = {"connection": chosen.name, "table": facts.pop("name"), **facts}
        return answers.render(answer, "columns", [column_item(c) for c in columns], answer_characters, "")

    def live_table(chosen: Connection, schema: str, name: str) -> tuple[dict, list]:
        """A readable table as the database holds it now: its name, kind, comment and `bound_by`, and its columns."""
        rule = connection_rules[chosen.name]
        columns = chosen.adapter.columns(schema, name)
        listed = [
            t
            for t in chosen.adapter.tables()
            if (guard.stored_name(t.schema, rule), guard.stored_name(t.name, rule)) == (schema, name)
        ]
        facts: dict = {"name": f"{schema}.{name}"}
        if listed:
            facts["kind"] = listed[0].kind
            if listed[0].comment:
                facts["comment"] = listed[0].comment
        facts |= large_facts(guard.large_table(schema, name, rule))
        return facts, columns

    def large_facts(large) -> dict:
        """What a query over a large table must do: `bound_by`, the column it bounds from below, and `narrowed_by`,
        the columns it holds to values it names — nothing for a table that is not large."""
        if large is None:
            return {}
        return {"bound_by": large.bound_by, **({"narrowed_by": list(large.narrowed_by)} if large.narrowed_by else {})}

    def cut_texts(node: Any, limit: int) -> Any:
        """A copy of a value with each text longer than `limit` characters cut to it, ending in `…`."""
        if isinstance(node, str):
            return node if len(node) <= limit else node[:limit] + "…"
        if isinstance(node, dict):
            return {key: cut_texts(value, limit) for key, value in node.items()}
        if isinstance(node, list):
            return [cut_texts(value, limit) for value in node]
        return copy.copy(node)

    def column_item(column, name: bool = True) -> dict:
        item = {"name": column.name} if name else {}
        item["type"] = column.type
        if column.nullable is not None:
            item["nullable"] = column.nullable
        if column.comment:
            item["comment"] = column.comment
        return item

    def carrying_instructions(function):
        """A tool whose description is followed by the server's instructions, when the configuration asks for it
        (`instructions_in_run_query`): a gateway that joins servers under one may pass their tools on and drop the
        instructions each gives when a client connects — and an agent reads a tool's description before calling it."""
        description = None
        if config and config.instructions_in_run_query:
            description = f"{function.__doc__}\n\n{CARRIED}\n\n{said}"  # the tool's own, unchanged, then them
        return server.tool(annotations=READ_ONLY, structured_output=False, description=description)(function)

    @carrying_instructions
    def run_query(sql: str, connection: str | None = None) -> str:
        """Run one read-only SELECT (WITH, UNION, joins, subqueries allowed) in the connection's SQL dialect.

        The query is checked before it runs: only readable tables, a large table read alone in its SELECT (joined as a
        subquery of its own) and bounded from below by its column (and held to named values of the columns it is
        narrowed by), no writes, settings or file and network functions. A refusal says why and how to fix it.

        Answers `columns` (name, type), `rows` as arrays in column order, and the provenance: `connection`, `sql` as
        it ran, `started_at` (UTC), `seconds`, `row_count`, `truncated`. Rows past the connection's limit are not
        read — `truncated` is then true and `note` says so: aggregate, filter or add LIMIT. Numbers are exact; a
        decimal JSON cannot hold exactly comes as a string. `connection` may be left out when there is only one."""
        chosen = pick(connection)
        try:
            checked = guard.check(sql, connection_rules[chosen.name])
            reading = chosen.adapter.engine_reading(checked.sql, seconds=chosen.limits.seconds, named=checked.tables)
            if reading.refusal:
                raise guard.Refused("engine_parse", reading.refusal)
            if reading.tables is not None:
                guard.check_engine_tables(reading.tables, checked, connection_rules[chosen.name])
        except AdapterError as error:
            raise ToolError(f"The database ({chosen.name}) could not read the query: {error}") from error
        except guard.Refused as refusal:
            raise ToolError(f"Refused by the guard ({refusal.code}): {refusal.message}.") from refusal

        limits = chosen.limits
        started_at = datetime.datetime.now(datetime.UTC)
        start = time.monotonic()
        try:
            result = chosen.adapter.run(checked.sql, max_rows=limits.rows, seconds=limits.seconds)
        except TimedOut as error:
            raise ToolError(
                f"Stopped after {limits.seconds:g} s, the time limit of {chosen.name}. Narrow the query: fewer days, "
                "fewer columns, an aggregate."
            ) from error
        except AdapterError as error:
            elapsed = time.monotonic() - start
            raise ToolError(f"The database ({chosen.name}) returned an error after {elapsed:.3f} s: {error}") from error
        elapsed = time.monotonic() - start

        answer: dict = {
            "connection": chosen.name,
            "sql": checked.sql,
            "started_at": started_at.isoformat(timespec="seconds"),
            "seconds": round(elapsed, 3),
            "columns": [{"name": column.name, "type": column.type} for column in result.columns],
            "row_count": len(result.rows),
            "truncated": result.more_rows,
        }
        if result.more_rows:
            answer["truncated_by"] = ["rows"]
            answer["note"] = (
                f"The query returned more than {limits.rows} rows, the limit of {chosen.name}; only the first "
                f"{limits.rows} were read. Aggregate, filter, or add LIMIT."
            )
        rows = [[answers.value(item) for item in row] for row in result.rows]
        return answers.render(answer, "rows", rows, answer_characters, "Select fewer columns, aggregate, or add LIMIT.")

    if not defines_metrics(config):  # a tool that could only answer "there are none" costs an agent a turn
        server.remove_tool("list_metrics")
        server.remove_tool("describe_metric")
    return server


def main(argv: list[str] | None = None) -> None:
    """The console entry point: check the configuration, then serve over stdio — or, as `import-dbt` and
    `import-database`, the importers."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["import-dbt"]:
        from .importers import dbt

        dbt.main(argv[1:])
        return
    if argv[:1] == ["import-database"]:
        from .importers import database

        database.main(argv[1:])
        return
    parser = argparse.ArgumentParser(
        prog="semantic-warehouse-mcp",
        description="A read-only MCP server for SQL databases with their semantics. Serves over stdio, or HTTP.",
    )
    parser.add_argument("--config", help=f"the configuration file (or set {CONFIG_VARIABLE})")
    parser.add_argument("--check", action="store_true", help="check the configuration, reach every database, exit")
    parser.add_argument("--offline", action="store_true", help="with --check: reach no database (for CI)")
    parser.add_argument("--http", action="store_true", help="serve over Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="with --http: the address to listen on (127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="with --http: the port (8000)")
    parser.add_argument("--path", default="/mcp", help="with --http: the MCP endpoint's path (/mcp)")
    parser.add_argument("--token-env", help="with --http: the variable holding the token every request must carry")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    path = args.config or os.environ.get(CONFIG_VARIABLE)
    if not path:
        parser.error(f"no configuration: pass --config <file> or set {CONFIG_VARIABLE}")
    if args.offline and not args.check:
        parser.error("--offline goes with --check: a server that serves reaches its databases")
    if args.http:
        try:  # before the databases are reached: a server that cannot be served safely never starts
            serving.token_of(args.token_env, args.host)
        except serving.HttpProblem as problem:
            print(f"semantic-warehouse-mcp: {problem}", file=sys.stderr)
            sys.exit(2)
    try:
        config = load(path, reach=not args.offline)
    except ConfigError as error:
        print(f"semantic-warehouse-mcp: {error}", file=sys.stderr)
        sys.exit(2)
    if args.check:
        offline = " without reaching its databases (sources held to `readable`, not looked up)" if args.offline else ""
        print(f"The configuration {config.source} is fine{offline}:")
        for connection in config.connections.values():
            readable = ", ".join(connection.readable)
            reached = "not reached" if args.offline else "reached"
            print(f"  - {connection.name} ({connection.engine}): {reached}; readable {readable}")
            for model in connection.models:
                datasets = model.document["datasets"]
                metrics = len(model.document.get("metrics", [])) + sum(len(d.get("metrics", [])) for d in datasets)
                counts = f"{len(datasets)} datasets, {metrics} metrics"
                print(f"    semantic model {model.name} (Ossie {model.version}): {counts}")
            if connection.left_out:
                print(f"    left out, the database holding no table of theirs: {', '.join(connection.left_out)}")
            for note in connection.notes:
                print(f"    note: {note}")
            print_team_files("    ", connection.context, connection.skills)
        for vendor, declared in config.vocabularies.items():
            print(f"  - vocabulary {vendor} ({declared.file}): {len(declared.keys)} keys")
        print_team_files("  - for every connection, ", config.context, config.skills)
        for repository in config.repositories.values():
            fetched = f"at {repository.current.commit[:12]}" if repository.current else "not fetched"
            print(f"  - {repository.described}: {fetched}; {', '.join(sorted(set(repository.paths)))}")
        return
    for line in start_log(config):  # said where the server's log is read
        print(line, file=sys.stderr)
    if config.repositories:
        signal.signal(signal.SIGTERM, stopped)
        repositories.keep_fresh(list(config.repositories.values()))
    if not args.http:
        build_server(config).run("stdio")
        return
    try:
        serving.serve(build_server(config), host=args.host, port=args.port, path=args.path, token_env=args.token_env)
    except serving.HttpProblem as problem:
        print(f"semantic-warehouse-mcp: {problem}", file=sys.stderr)
        sys.exit(2)


def stopped(number: int, frame: object) -> None:
    """SIGTERM: the copies of repositories removed, then out at once — over stdio a normal exit would wait for the
    thread reading standard input, which a stopped server never closes. Over HTTP, after uvicorn has finished."""
    repositories.remove_copies()
    os._exit(128 + number)


def start_log(config: Config) -> list[str]:
    """What the server says in its log as it starts: each connection's notes, and the datasets left out — counted, the
    first few named (`--check` names every one)."""
    lines = []
    for connection in config.connections.values():
        lines += [f"semantic-warehouse-mcp: {connection.name}: note: {note}" for note in connection.notes]
        if connection.left_out:
            named = ", ".join(connection.left_out[:LOG_NAMES]) + (", …" if len(connection.left_out) > LOG_NAMES else "")
            lines.append(
                f"semantic-warehouse-mcp: {connection.name}: {len(connection.left_out)} datasets left out, no table of "
                f"theirs: {named} (`--check` names every one)"
            )
    return lines


def print_team_files(prefix: str, documents: tuple, skill_folders: tuple) -> None:
    """What `--check` says of a scope's context documents and skills: how many documents, which skills — those in a
    repository only once it is fetched."""
    if any(place.repository is not None and place.repository.current is None for place in documents + skill_folders):
        return
    documents, skill_folders = repositories.current(documents), repositories.current(skill_folders)
    if documents:
        count = len(context.documents(documents))
        print(f"{prefix}context: {count} document{'' if count == 1 else 's'}")
    if skill_folders:
        print(f"{prefix}skills: {', '.join(skills.skills(skill_folders))}")


if __name__ == "__main__":
    main()
