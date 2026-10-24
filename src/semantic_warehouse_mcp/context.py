"""Context documents: what a team writes for agents beside its data — its rules, its kitchen — served as written."""

from __future__ import annotations

import pathlib

# What counts as a document in a context folder; anything else there (and hidden files and folders) is left alone.
DOCUMENT_SUFFIXES = {".md", ".markdown", ".txt"}


class ContextError(Exception):
    pass


def documents(paths: tuple[pathlib.Path, ...]) -> dict[str, pathlib.Path]:
    """The documents of a list of files and folders, by name: a file by its own name, a folder's files as
    `<folder>/<path inside it>`. Read anew on every call, so an edited or added document is served without a restart."""
    found: dict[str, pathlib.Path] = {}
    for path in paths:
        if path.is_file():
            entries = [(path.name, path)]
        elif path.is_dir():
            entries = [
                (f"{path.name}/{file.relative_to(path).as_posix()}", file)
                for file in sorted(path.rglob("*"))
                if file.is_file()
                and file.suffix.lower() in DOCUMENT_SUFFIXES
                and not any(part.startswith(".") for part in file.relative_to(path).parts)
            ]
        else:
            raise ContextError(f"{path} does not exist")
        for name, file in entries:
            if name in found and found[name] != file:
                raise ContextError(f"two documents are named {name!r}: {found[name]} and {file}")
            found[name] = file
    return found


def title(text: str) -> str:
    """A document's first heading, or its first line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("#"):
            return line.lstrip("#").strip()[:160]
    return lines[0][:160] if lines else ""
