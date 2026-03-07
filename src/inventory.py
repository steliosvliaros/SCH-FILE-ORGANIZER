from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class InventoryConfig:
    hash_algorithm: str = "blake2b"
    hash_size_bytes: int = 16
    chunk_size: int = 1024 * 1024
    follow_symlinks: bool = False


def hash_file(path: Path, algorithm: str = "blake2b", hash_size_bytes: int = 16, chunk_size: int = 1024 * 1024) -> str:
    if algorithm == "blake2b":
        h = hashlib.blake2b(digest_size=hash_size_bytes)
    elif algorithm == "sha256":
        h = hashlib.sha256()
    else:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def iter_files(scan_root: Path, follow_symlinks: bool = False) -> Iterable[Path]:
    for path in scan_root.rglob("*"):
        if path.is_file():
            if path.is_symlink() and not follow_symlinks:
                continue
            yield path


def build_inventory(scan_root: str | Path, config: InventoryConfig | None = None) -> pd.DataFrame:
    config = config or InventoryConfig()
    root = Path(scan_root).resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    rows: list[dict] = []
    for path in iter_files(root, follow_symlinks=config.follow_symlinks):
        stat = path.stat()
        rel = path.relative_to(root)
        parts = rel.parts
        rows.append(
            {
                "scan_root": str(root),
                "absolute_path": str(path),
                "relative_path": str(rel),
                "parent_relative": str(rel.parent) if rel.parent != Path(".") else "",
                "filename": path.name,
                "stem": path.stem,
                "suffix": path.suffix.lower(),
                "size_bytes": stat.st_size,
                "modified_at": pd.Timestamp(stat.st_mtime, unit="s"),
                "created_at": pd.Timestamp(stat.st_ctime, unit="s"),
                "depth_segments": len(parts),
                "path_length": len(str(path)),
                "filename_length": len(path.name),
                "is_hidden": path.name.startswith("."),
                "is_symlink": path.is_symlink(),
                "top_segment": parts[0] if parts else "",
                "hash": hash_file(
                    path,
                    algorithm=config.hash_algorithm,
                    hash_size_bytes=config.hash_size_bytes,
                    chunk_size=config.chunk_size,
                ),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df.sort_values(["relative_path"]).reset_index(drop=True)
    duplicate_counts = df.groupby("hash")["hash"].transform("size")
    df["duplicate_group_size"] = duplicate_counts
    df["is_duplicate_hash"] = duplicate_counts > 1
    return df


def save_inventory(df: pd.DataFrame, output_base: str | Path) -> tuple[Path, Path]:
    output_base = Path(output_base)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_base.with_suffix(".csv")
    parquet_path = output_base.with_suffix(".parquet")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_parquet(parquet_path, index=False)
    return csv_path, parquet_path


def summarize_inventory(df: pd.DataFrame) -> dict[str, object]:
    if df.empty:
        return {
            "file_count": 0,
            "total_size_bytes": 0,
            "duplicate_files": 0,
            "duplicate_groups": 0,
            "max_path_length": 0,
        }

    return {
        "file_count": int(len(df)),
        "total_size_bytes": int(df["size_bytes"].sum()),
        "duplicate_files": int(df["is_duplicate_hash"].sum()),
        "duplicate_groups": int((df.groupby("hash").size() > 1).sum()),
        "max_path_length": int(df["path_length"].max()),
        "max_depth_segments": int(df["depth_segments"].max()),
    }
