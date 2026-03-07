from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import blake2b
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd


@dataclass(slots=True)
class InventoryRecord:
    absolute_path: str
    root_path: str
    relative_path: str
    parent_path: str
    name: str
    stem: str
    extension: str
    size_bytes: int
    modified_utc: str
    created_utc: str
    depth: int
    content_hash: str | None
    is_hidden: bool
    is_symlink: bool

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_IGNORE_DIRS = {".git", "__pycache__", ".ipynb_checkpoints"}
DEFAULT_IGNORE_FILES = {"Thumbs.db", ".DS_Store", "desktop.ini"}


class InventoryError(Exception):
    """Raised when the inventory scanner cannot proceed."""


class InventoryScanner:
    def __init__(
        self,
        root_path: str | Path,
        *,
        include_hidden: bool = False,
        hash_files: bool = True,
        hash_algorithm: str = "blake2b",
        hash_chunk_size: int = 1024 * 1024,
        follow_symlinks: bool = False,
    ) -> None:
        self.root_path = Path(root_path).expanduser().resolve()
        self.include_hidden = include_hidden
        self.hash_files = hash_files
        self.hash_algorithm = hash_algorithm
        self.hash_chunk_size = hash_chunk_size
        self.follow_symlinks = follow_symlinks

        if not self.root_path.exists():
            raise FileNotFoundError(f"Scan root not found: {self.root_path}")
        if not self.root_path.is_dir():
            raise InventoryError(f"Scan root must be a directory: {self.root_path}")

    def scan(self) -> list[InventoryRecord]:
        return list(self.iter_records())

    def iter_records(self) -> Iterator[InventoryRecord]:
        for path in self._iter_files(self.root_path):
            stat = path.stat(follow_symlinks=self.follow_symlinks)
            relative_path = path.relative_to(self.root_path)
            parent_relative = relative_path.parent.as_posix() if relative_path.parent != Path(".") else ""
            yield InventoryRecord(
                absolute_path=str(path),
                root_path=str(self.root_path),
                relative_path=relative_path.as_posix(),
                parent_path=parent_relative,
                name=path.name,
                stem=path.stem,
                extension=path.suffix.lower(),
                size_bytes=stat.st_size,
                modified_utc=_iso_utc(stat.st_mtime),
                created_utc=_iso_utc(stat.st_ctime),
                depth=len(relative_path.parts),
                content_hash=self._hash_file(path) if self.hash_files else None,
                is_hidden=_is_hidden(path),
                is_symlink=path.is_symlink(),
            )

    def to_dataframe(self) -> pd.DataFrame:
        rows = [record.to_dict() for record in self.scan()]
        return pd.DataFrame(rows)

    def write_outputs(
        self,
        *,
        csv_path: str | Path | None = None,
        parquet_path: str | Path | None = None,
    ) -> pd.DataFrame:
        frame = self.to_dataframe()
        if csv_path:
            csv_path = Path(csv_path)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(csv_path, index=False)
        if parquet_path:
            parquet_path = Path(parquet_path)
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(parquet_path, index=False)
        return frame

    def _iter_files(self, root: Path) -> Iterable[Path]:
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            if not self.follow_symlinks and path.is_symlink():
                continue
            if self._should_skip(path):
                continue
            yield path

    def _should_skip(self, path: Path) -> bool:
        parts = set(path.parts)
        if parts.intersection(DEFAULT_IGNORE_DIRS):
            return True
        if path.name in DEFAULT_IGNORE_FILES and not self.include_hidden:
            return False
        return (not self.include_hidden) and _is_hidden(path)

    def _hash_file(self, path: Path) -> str | None:
        if self.hash_algorithm != "blake2b":
            raise InventoryError(f"Unsupported hash algorithm: {self.hash_algorithm}")

        hasher = blake2b(digest_size=16)
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(self.hash_chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
            return hasher.hexdigest()
        except (OSError, PermissionError):
            return None



def _iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()



def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)
