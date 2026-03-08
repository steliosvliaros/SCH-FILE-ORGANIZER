from __future__ import annotations

import hashlib
import os
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


def _to_os_path(path: str | Path) -> str:
    """Return an OS path string suitable for Win32 long-path access when needed."""
    raw = str(path)
    if os.name != "nt":
        return raw

    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\?\\"):
        return normalized
    if normalized.startswith("\\\\"):
        # UNC path: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + normalized.lstrip("\\")
    return "\\\\?\\" + normalized


def hash_file(path: Path, algorithm: str = "blake2b", hash_size_bytes: int = 16, chunk_size: int = 1024 * 1024) -> str:
    if algorithm == "blake2b":
        h = hashlib.blake2b(digest_size=hash_size_bytes)
    elif algorithm == "sha256":
        h = hashlib.sha256()
    else:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    with open(_to_os_path(path), "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def iter_files(scan_root: Path, follow_symlinks: bool = False) -> Iterable[Path]:
    stack = [scan_root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(_to_os_path(current)) as entries:
                for entry in entries:
                    child = current / entry.name
                    try:
                        is_link = entry.is_symlink()
                        if is_link and not follow_symlinks:
                            continue
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            stack.append(child)
                            continue
                        if entry.is_file(follow_symlinks=follow_symlinks):
                            yield child
                    except OSError:
                        # Skip entries that disappear or cannot be stat'ed mid-scan.
                        continue
        except OSError:
            # Skip directories that are inaccessible, missing, or exceed normal Win32 handling.
            continue




def _derive_filename(path_like: str) -> str:
    raw = str(path_like or '').replace('\\', '/')
    return Path(raw).name


def _derive_parent_relative(path_like: str) -> str:
    raw = str(path_like or '').replace('\\', '/')
    parent = str(Path(raw).parent)
    return '' if parent in {'.', ''} else parent.replace('\\', '/')


def ensure_inventory_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Backfill expected inventory columns for older parquet/csv outputs."""
    out = df.copy()
    if out.empty:
        return out

    if 'relative_path' not in out.columns and 'absolute_path' in out.columns:
        out['relative_path'] = out['absolute_path'].astype(str)

    if 'filename' not in out.columns:
        out['filename'] = out['relative_path'].astype(str).map(_derive_filename)
    else:
        missing = out['filename'].isna() | (out['filename'].astype(str).str.strip() == '')
        out.loc[missing, 'filename'] = out.loc[missing, 'relative_path'].astype(str).map(_derive_filename)

    if 'suffix' not in out.columns:
        out['suffix'] = out['filename'].astype(str).map(lambda s: Path(s).suffix.lower())
    else:
        missing = out['suffix'].isna() | (out['suffix'].astype(str).str.strip() == '')
        out.loc[missing, 'suffix'] = out.loc[missing, 'filename'].astype(str).map(lambda s: Path(s).suffix.lower())
    out['suffix'] = out['suffix'].fillna('').astype(str).str.lower()

    if 'stem' not in out.columns:
        out['stem'] = out['filename'].astype(str).map(lambda s: Path(s).stem)

    if 'parent_relative' not in out.columns:
        out['parent_relative'] = out['relative_path'].astype(str).map(_derive_parent_relative)

    if 'path_length' not in out.columns:
        base_col = 'absolute_path' if 'absolute_path' in out.columns else 'relative_path'
        out['path_length'] = out[base_col].astype(str).str.len()

    if 'filename_length' not in out.columns:
        out['filename_length'] = out['filename'].astype(str).str.len()

    if 'depth_segments' not in out.columns:
        out['depth_segments'] = out['relative_path'].astype(str).map(lambda s: len(Path(str(s).replace('\\', '/')).parts))

    if 'is_hidden' not in out.columns:
        out['is_hidden'] = out['filename'].astype(str).str.startswith('.')

    if 'is_symlink' not in out.columns:
        out['is_symlink'] = False

    if 'top_segment' not in out.columns:
        out['top_segment'] = out['relative_path'].astype(str).map(lambda s: Path(str(s).replace('\\', '/')).parts[0] if Path(str(s).replace('\\', '/')).parts else '')

    if 'hash' in out.columns and 'is_duplicate_hash' not in out.columns:
        dup_sizes = out.groupby('hash', dropna=False)['hash'].transform('size')
        out['is_duplicate_hash'] = dup_sizes > 1
        out['duplicate_group_size'] = dup_sizes

    return out
def build_inventory(scan_root: str | Path, config: InventoryConfig | None = None) -> pd.DataFrame:
    config = config or InventoryConfig()
    root = Path(scan_root).resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    rows: list[dict] = []
    for path in iter_files(root, follow_symlinks=config.follow_symlinks):
        try:
            stat = os.stat(_to_os_path(path), follow_symlinks=config.follow_symlinks)
        except OSError:
            continue
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

    df = ensure_inventory_schema(df)
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
