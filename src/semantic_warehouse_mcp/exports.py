"""Exports: a query's rows written to a file in storage the team names, so a person can have them without the rows
passing through an agent's context (docs/reference/running.md, "Exports").

`export_query` runs one checked SELECT, writes the whole answer as a CSV file, and answers with a link, the size and a
few rows to check it by. The server holds no data of its own: the file is written to the configured storage and the
link is the storage's own, signed and short-lived. Two kinds of storage: S3-compatible object storage, and a
directory (for a machine's own use and for tests)."""

from __future__ import annotations

import csv
import datetime
import os
import pathlib
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import answers, files
from .adapters import AdapterError

# A file name is made of these only: a connection's name and a date. The caller's name in a key is cleaned the same way.
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
# A text cell starting with one of these is read by a spreadsheet as a formula: the file's reader would run what the
# database's text says. Such a cell is written with a leading quote, as a spreadsheet writes a text that looks like one.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
CELL_PREVIEW = 200
PREVIEW_ROWS = 5


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class S3Storage(_Strict):
    type: Literal["s3"]
    bucket: Annotated[str, Field(min_length=1)]
    prefix: str = "exports/"
    endpoint_url: str | None = None
    region: str | None = None
    # The variables holding the keys, never the keys. Both left out: the SDK's own sources (a role, the environment).
    access_key_env: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")] | None = None
    secret_key_env: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")] | None = None


class DirectoryStorage(_Strict):
    type: Literal["directory"]
    path: Annotated[str, Field(min_length=1)]
    # What a file's link starts with, if the directory is served somewhere; without it the link is the file's path.
    url_prefix: str | None = None


StorageEntry = Annotated[S3Storage | DirectoryStorage, Field(discriminator="type")]


class ExportLimits(_Strict):
    rows: Annotated[int, Field(gt=0, le=500_000)] = 100_000
    megabytes: Annotated[int, Field(gt=0, le=1024)] = 100


class ExportsEntry(_Strict):
    storage: StorageEntry
    limits: ExportLimits = ExportLimits()
    link_minutes: Annotated[int, Field(ge=1, le=1440)] = 60


class ExportError(Exception):
    """An export that could not be made; the message is for the agent, and never holds a key."""


class Storage(ABC):
    @abstractmethod
    def put(self, source: pathlib.Path, key: str) -> None:
        """Store the file under `key`."""

    @abstractmethod
    def link(self, key: str, file_name: str, seconds: int) -> str:
        """A link that fetches the file for `seconds`, saving it as `file_name`."""


class Directory(Storage):
    def __init__(self, settings: DirectoryStorage, base_dir: pathlib.Path) -> None:
        self.root = (base_dir / settings.path).resolve()
        self.url_prefix = settings.url_prefix

    def put(self, source: pathlib.Path, key: str) -> None:
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ExportError("the file's place is outside the export directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def link(self, key: str, file_name: str, seconds: int) -> str:
        if self.url_prefix:
            return self.url_prefix.rstrip("/") + "/" + key
        return str(self.root / key)


class S3(Storage):
    """Object storage over the S3 API (AWS, or any that speaks it). The driver comes with the `exports` extra."""

    def __init__(self, settings: S3Storage) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise ExportError(
                "the S3 driver (boto3) is not installed: pip install 'semantic-warehouse-mcp[exports]'"
            ) from error
        self.bucket = settings.bucket
        keys: dict[str, str] = {}
        for argument, variable in (
            ("aws_access_key_id", settings.access_key_env),
            ("aws_secret_access_key", settings.secret_key_env),
        ):
            if variable:
                value = os.environ.get(variable)
                if not value:
                    raise ExportError(f"the variable {files.variable(variable, 'exports')} is not set (or empty)")
                keys[argument] = value
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            region_name=settings.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            **keys,
        )

    def put(self, source: pathlib.Path, key: str) -> None:
        try:
            self.client.upload_file(str(source), self.bucket, key, ExtraArgs={"ContentType": "text/csv; charset=utf-8"})
        except Exception as error:  # noqa: BLE001 - the SDK's errors are many; the agent is told which step failed
            raise ExportError(
                f"the file could not be stored ({type(error).__name__}); ask whoever runs the server"
            ) from None

    def link(self, key: str, file_name: str, seconds: int) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{file_name}"',
                "ResponseContentType": "text/csv; charset=utf-8",
            },
            ExpiresIn=seconds,
        )


def storage_of(entry: ExportsEntry, base_dir: pathlib.Path) -> Storage:
    if isinstance(entry.storage, DirectoryStorage):
        return Directory(entry.storage, base_dir)
    return S3(entry.storage)


def cell(item: Any) -> str:
    """A value as a CSV cell: empty for NULL, JSON for a list or a mapping, and a text that a spreadsheet would run as
    a formula with a leading quote."""
    shown = answers.value(item)
    if shown is None:
        return ""
    if isinstance(shown, bool):
        return "true" if shown else "false"
    if isinstance(shown, (list, dict)):
        return answers.dumps(shown)
    text = str(shown)
    if isinstance(shown, str) and text.startswith(FORMULA_STARTS) and not NUMBER.fullmatch(text):
        return "'" + text
    return text


def write_csv(path: pathlib.Path, names: list[str], rows: list[tuple[Any, ...]], max_bytes: int) -> int:
    """The rows as a UTF-8 CSV file with a header (a byte-order mark first: a spreadsheet reads non-Latin text right
    only with one). Refuses past `max_bytes`; returns the size."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([cell(name) for name in names])
        for number, row in enumerate(rows, 1):
            writer.writerow([cell(item) for item in row])
            if number % 1000 == 0:
                handle.flush()
                if path.stat().st_size > max_bytes:
                    break
    if path.stat().st_size > max_bytes:
        raise ExportError(
            f"the file would pass {max_bytes // 2**20} MB, the limit: select fewer columns or narrow the query"
        )
    return path.stat().st_size


def preview(rows: list[tuple[Any, ...]]) -> list[list[Any]]:
    """The first rows, each text cut short: enough for an agent to check the file by, never the data itself."""
    shown = []
    for row in rows[:PREVIEW_ROWS]:
        values = [answers.value(item) for item in row]
        shown.append(
            [v if not isinstance(v, str) or len(v) <= CELL_PREVIEW else v[:CELL_PREVIEW] + "…" for v in values]
        )
    return shown


def key_of(prefix: str, caller: str | None, now: datetime.datetime) -> str:
    """Where a file goes: the day, then who asked, then a name nobody can guess."""
    who = UNSAFE.sub("_", caller or "anonymous").strip("._") or "anonymous"
    return f"{prefix.lstrip('/')}{now:%Y-%m-%d}/{who}/{uuid.uuid4().hex}.csv"


__all__ = ["AdapterError", "ExportError", "ExportsEntry", "Storage", "storage_of", "write_csv"]
