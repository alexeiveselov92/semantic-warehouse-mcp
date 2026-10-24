"""Skills: a team's ways of doing a task, each a folder holding a SKILL.md (the Agent Skills format,
https://agentskills.io/specification) — served as written, with the files beside it."""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import yaml

from . import files

SKILL_FILE = "SKILL.md"

# The format's rules for a name: 1–64 lowercase letters, digits and hyphens, no hyphen at either end, none doubled.
NAME = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)$")
DESCRIPTION_CHARACTERS = 1024


class SkillError(Exception):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    folder: pathlib.Path

    @property
    def file(self) -> pathlib.Path:
        return self.folder / SKILL_FILE


def skills(paths: tuple[pathlib.Path, ...]) -> dict[str, Skill]:
    """The skills of a list of folders, by name: a folder holding a SKILL.md is one skill, any other folder holds a
    skill in each folder inside it that holds one (hidden folders left out). Read anew on every call, so an edited or
    added skill is served without a restart. Raises SkillError naming the skill and what is wrong."""
    found: dict[str, Skill] = {}
    for path in paths:
        if not path.is_dir():
            raise SkillError(f"{path} is not a folder")
        if (path / SKILL_FILE).is_file():
            folders = [path]
        else:
            folders = [
                inside
                for inside in sorted(path.iterdir())
                if inside.is_dir() and not inside.name.startswith(".") and (inside / SKILL_FILE).is_file()
            ]
            if not folders:
                raise SkillError(f"{path} holds no skill: no {SKILL_FILE} in it or in a folder inside it")
        for folder in folders:
            skill = read(folder)
            if skill.name in found and found[skill.name].folder != folder:
                raise SkillError(f"two skills are named {skill.name!r}: {found[skill.name].folder} and {folder}")
            found[skill.name] = skill
    return found


def read(folder: pathlib.Path) -> Skill:
    """A skill's name and description, from its SKILL.md's frontmatter, checked by the format's rules."""
    where = folder / SKILL_FILE
    header = frontmatter(text_of(where, where))
    if header is None:
        raise SkillError(f"{where}: no frontmatter — the file must start with `---`, `name` and `description`")
    try:
        data = files.read_yaml(header)
    except yaml.YAMLError as error:
        raise SkillError(f"{where}: the frontmatter is not valid YAML: {files.yaml_problem(error)}") from error
    if not isinstance(data, dict):
        raise SkillError(f"{where}: the frontmatter must be a mapping holding `name` and `description`")
    name, description = data.get("name"), data.get("description")
    problems = []
    if not isinstance(name, str) or not NAME.match(name):
        problems.append(
            "`name` must be 1–64 lowercase letters, digits and hyphens, with no hyphen at either end and none doubled"
        )
    elif name != folder.name:
        problems.append(f"`name` must be the folder's name, {folder.name!r}")
    if not isinstance(description, str) or not description.strip():
        problems.append("`description` must be text saying what the skill does and when to use it")
    elif len(description) > DESCRIPTION_CHARACTERS:
        problems.append(f"`description` must be at most {DESCRIPTION_CHARACTERS} characters ({len(description)})")
    if problems:
        raise SkillError(f"{where}: {'; '.join(problems)}")
    return Skill(name=name, description=description, folder=folder)


def frontmatter(text: str) -> str | None:
    """The YAML between a file's opening `---` line and the next one, or None when it has none."""
    lines = text.removeprefix("\ufeff").splitlines(keepends=True)  # a byte order mark before it is no part of it
    if not lines or lines[0].rstrip() != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            return "".join(lines[1:index])
    return None


def files_of(skill: Skill) -> tuple[dict[str, pathlib.Path], list[str]]:
    """The files of a skill beside its SKILL.md, by their path inside its folder: the text ones to serve, and the names
    of the others (images, archives), which are not. Hidden files and folders left out."""
    text: dict[str, pathlib.Path] = {}
    other: list[str] = []
    for file in sorted(skill.folder.rglob("*")):
        inside = file.relative_to(skill.folder)
        if not file.is_file() or any(part.startswith(".") for part in inside.parts) or inside.as_posix() == SKILL_FILE:
            continue
        if not files.nameable(inside.as_posix()):
            continue  # a name that is not UTF-8 could be listed, but never asked for
        try:
            text_of(file, file)
        except SkillError:
            other.append(inside.as_posix())
        else:
            text[inside.as_posix()] = file
    return text, other


def text_of(file: pathlib.Path, where: object) -> str:
    """A file's text: UTF-8 without a NUL character — what a skill's text files are; anything else is not served."""
    try:
        data = file.read_bytes()
    except OSError as error:
        raise SkillError(f"{where}: cannot read the file: {error.strerror or error}") from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillError(f"{where}: not UTF-8 text (byte {error.start} is not)") from error
    if "\x00" in text:
        raise SkillError(f"{where}: not text (it holds a NUL character)")
    return text
