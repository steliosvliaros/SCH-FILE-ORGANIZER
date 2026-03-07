from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from .inventory import ensure_inventory_schema


@dataclass(frozen=True)
class ReviewPaths:
    inventory_path: Path | None
    classification_path: Path | None
    text_path: Path | None


KEY_CANDIDATES: list[list[str]] = [
    ["relative_path"],
    ["absolute_path"],
    ["relative_path", "hash"],
    ["absolute_path", "hash"],
]


def find_latest_output(
    outputs_dir: str | Path,
    prefix: str,
    suffix: str = ".parquet",
    exclude_prefixes: Iterable[str] | None = None,
) -> Path | None:
    outputs_path = Path(outputs_dir)
    exclude_prefixes = tuple(exclude_prefixes or ())
    matches = []
    for p in outputs_path.glob(f"*{suffix}"):
        stem = p.stem
        if not stem.startswith(prefix):
            continue
        if any(stem.startswith(ex) for ex in exclude_prefixes):
            continue
        matches.append(p)
    matches = sorted(matches, key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def detect_latest_outputs(outputs_dir: str | Path) -> ReviewPaths:
    outputs_path = Path(outputs_dir)
    return ReviewPaths(
        inventory_path=find_latest_output(outputs_path, "inventory_", exclude_prefixes=["inventory_with_text_"]),
        classification_path=find_latest_output(outputs_path, "rule_classification_"),
        text_path=find_latest_output(outputs_path, "inventory_with_text_"),
    )


def load_optional_parquet(path: str | Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return pd.read_parquet(file_path)


def _merge_keys(left: pd.DataFrame, right: pd.DataFrame) -> list[str]:
    for keys in KEY_CANDIDATES:
        if all(key in left.columns for key in keys) and all(key in right.columns for key in keys):
            return keys
    raise KeyError("No compatible merge keys found between dataframes")


def _suffix_columns(df: pd.DataFrame, suffix: str, protected: Iterable[str]) -> pd.DataFrame:
    protected_set = set(protected)
    rename_map = {c: f"{c}{suffix}" for c in df.columns if c not in protected_set}
    return df.rename(columns=rename_map)


def _promote_if_missing(df: pd.DataFrame, target: str, source: str) -> pd.DataFrame:
    if target not in df.columns and source in df.columns:
        df[target] = df[source]
    return df


def build_review_frame(
    inventory_df: pd.DataFrame | None = None,
    classification_df: pd.DataFrame | None = None,
    text_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if inventory_df is None and classification_df is None and text_df is None:
        raise ValueError("At least one dataframe is required")

    inv = ensure_inventory_schema(inventory_df) if inventory_df is not None else None
    cls = classification_df.copy() if classification_df is not None else None
    txt = text_df.copy() if text_df is not None else None

    if cls is not None:
        base = cls.copy()
    elif txt is not None:
        base = txt.copy()
    elif inv is not None:
        base = inv.copy()
    else:
        raise ValueError("Unable to determine base dataframe")

    if inv is not None and base is not inv:
        inv_s = _suffix_columns(inv, "_inv", protected=["relative_path", "absolute_path", "hash"])
        keys = _merge_keys(base, inv_s)
        extra_cols = [c for c in inv_s.columns if c not in set(keys) and c not in base.columns]
        if extra_cols:
            base = base.merge(inv_s[keys + extra_cols], on=keys, how="left")

    if txt is not None and base is not txt:
        txt_s = _suffix_columns(txt, "_txt", protected=["relative_path", "absolute_path", "hash"])
        keys = _merge_keys(base, txt_s)
        extra_cols = [c for c in txt_s.columns if c not in set(keys) and c not in base.columns]
        if extra_cols:
            base = base.merge(txt_s[keys + extra_cols], on=keys, how="left")

    base = ensure_inventory_schema(base)
    for target, source in [
        ("text_status", "text_status_txt"),
        ("text_source", "text_source_txt"),
        ("text_error", "text_error_txt"),
        ("extracted_chars", "extracted_chars_txt"),
        ("has_extracted_text", "has_extracted_text_txt"),
        ("text_preview", "text_preview_txt"),
        ("extracted_text", "extracted_text_txt"),
    ]:
        base = _promote_if_missing(base, target, source)

    if "rule_status" not in base.columns:
        base["rule_status"] = "not_classified"
    if "rule_reason" not in base.columns:
        base["rule_reason"] = "classification output not loaded"
    if "rule_confidence" not in base.columns:
        base["rule_confidence"] = pd.NA
    if "text_status" not in base.columns:
        base["text_status"] = "not_extracted"
    if "text_source" not in base.columns:
        base["text_source"] = "not_extracted"
    if "text_error" not in base.columns:
        base["text_error"] = pd.NA
    if "extracted_chars" not in base.columns:
        base["extracted_chars"] = 0
    if "has_extracted_text" not in base.columns:
        base["has_extracted_text"] = False

    base["size_mb"] = (pd.to_numeric(base.get("size_bytes", 0), errors="coerce").fillna(0) / (1024 * 1024)).round(3)
    base["needs_manual_review"] = base["rule_status"].eq("review")
    base["has_text_error"] = base["text_status"].eq("error")
    base["is_probably_binary"] = base["text_status"].isin(["unsupported", "empty"])
    base["long_path_warning"] = pd.to_numeric(base.get("path_length", 0), errors="coerce").fillna(0).astype(int) >= 220
    base["long_filename_warning"] = pd.to_numeric(base.get("filename_length", 0), errors="coerce").fillna(0).astype(int) >= 110
    return base


def review_summary(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {
            "rows": 0,
            "duplicates": 0,
            "junk_candidates": 0,
            "manual_review": 0,
            "text_errors": 0,
            "long_paths": 0,
        }
    return {
        "rows": int(len(frame)),
        "duplicates": int(frame.get("is_duplicate_hash", False).fillna(False).sum()),
        "junk_candidates": int(frame.get("rule_status", pd.Series(dtype=object)).eq("archive_or_delete_candidate").sum()),
        "manual_review": int(frame.get("needs_manual_review", False).fillna(False).sum()),
        "text_errors": int(frame.get("has_text_error", False).fillna(False).sum()),
        "long_paths": int(frame.get("long_path_warning", False).fillna(False).sum()),
    }
