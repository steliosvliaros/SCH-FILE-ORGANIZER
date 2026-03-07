from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd

from .inventory import ensure_inventory_schema


@dataclass(frozen=True)
class ManifestConfig:
    executable_actions: tuple[str, ...] = (
        "move_to_policy_folder",
        "move_to_superseded_folder",
        "move_to_duplicate_folder",
        "move_to_deprecated_folder",
    )
    include_keep_register: bool = True
    block_target_collisions: bool = True
    require_target_path: bool = True
    require_changed_path: bool = True


@dataclass(frozen=True)
class ManifestBundle:
    executable_manifest: pd.DataFrame
    keep_register: pd.DataFrame
    review_queue: pd.DataFrame
    blocked_manifest: pd.DataFrame
    rollback_manifest: pd.DataFrame


PLAN_DEFAULTS: dict[str, Any] = {
    "planner_action": "manual_review",
    "planner_reason": "planner output missing",
    "planner_confidence": 0.0,
    "planner_ready": False,
    "planner_needs_user_input": True,
    "planner_target_subpath": pd.NA,
    "planner_target_relative_path": pd.NA,
    "planner_target_full_path": pd.NA,
    "planner_would_change_path": False,
    "planner_asset_root": pd.NA,
    "planner_root_mode": "ASSETS",
}


def _safe_series(df: pd.DataFrame, name: str, default: Any = None) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([default] * len(df), index=df.index)



def normalize_plan_schema(df: pd.DataFrame) -> pd.DataFrame:
    out = ensure_inventory_schema(df)
    for col, default in PLAN_DEFAULTS.items():
        if col not in out.columns:
            out[col] = default
    if out.empty:
        return out
    out["planner_confidence"] = pd.to_numeric(out["planner_confidence"], errors="coerce").fillna(0.0)
    for col in ["planner_ready", "planner_needs_user_input", "planner_would_change_path"]:
        out[col] = pd.Series(out[col], index=out.index).fillna(False).astype(bool)
    return out



def _derive_target_parent(path_like: str | None) -> str:
    if not path_like:
        return ""
    raw = str(path_like).replace("\\", "/")
    parent = str(PurePosixPath(raw).parent)
    return "" if parent in {".", ""} else parent



def _make_operation_id(row: pd.Series) -> str:
    rel = str(row.get("relative_path") or "")
    target = str(row.get("planner_target_relative_path") or "")
    action = str(row.get("planner_action") or "")
    hashed = abs(hash((rel, target, action))) % 10_000_000_000
    return f"op_{hashed:010d}"



def _build_executable_manifest(frame: pd.DataFrame, config: ManifestConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    action_mask = frame["planner_action"].isin(config.executable_actions)
    ready_mask = frame["planner_ready"] & ~frame["planner_needs_user_input"]
    target_mask = frame["planner_target_relative_path"].notna() if config.require_target_path else True
    changed_mask = frame["planner_would_change_path"] if config.require_changed_path else True

    candidates = frame[action_mask & ready_mask & target_mask & changed_mask].copy()
    if candidates.empty:
        empty = frame.iloc[0:0].copy()
        for col in [
            "execution_operation_id",
            "execution_action",
            "execution_status",
            "execution_blocked",
            "execution_block_reason",
            "execution_target_parent",
            "execution_source_relative_path",
            "execution_target_relative_path",
            "execution_source_full_path",
            "execution_target_full_path",
        ]:
            if col not in empty.columns:
                empty[col] = pd.Series(dtype=object)
        return empty, empty.copy()

    candidates["execution_operation_id"] = candidates.apply(_make_operation_id, axis=1)
    candidates["execution_action"] = "move"
    candidates["execution_status"] = "pending"
    candidates["execution_blocked"] = False
    candidates["execution_block_reason"] = pd.NA
    candidates["execution_target_parent"] = candidates["planner_target_relative_path"].astype(str).map(_derive_target_parent)
    candidates["execution_source_relative_path"] = candidates["relative_path"]
    candidates["execution_target_relative_path"] = candidates["planner_target_relative_path"]
    candidates["execution_source_full_path"] = _safe_series(candidates, "absolute_path", pd.NA)
    candidates["execution_target_full_path"] = candidates["planner_target_full_path"]

    blocked_rows: list[pd.DataFrame] = []
    if config.block_target_collisions:
        dup_mask = candidates["execution_target_relative_path"].duplicated(keep=False)
        if dup_mask.any():
            blocked = candidates[dup_mask].copy()
            blocked["execution_blocked"] = True
            blocked["execution_block_reason"] = "target_path_collision_within_manifest"
            blocked_rows.append(blocked)
            candidates = candidates[~dup_mask].copy()

    blocked_manifest = pd.concat(blocked_rows, ignore_index=True) if blocked_rows else candidates.iloc[0:0].copy()
    return candidates.reset_index(drop=True), blocked_manifest.reset_index(drop=True)



def _build_keep_register(frame: pd.DataFrame, config: ManifestConfig) -> pd.DataFrame:
    if not config.include_keep_register:
        return frame.iloc[0:0].copy()
    keep = frame[
        frame["planner_action"].eq("keep_in_place")
        & frame["planner_ready"]
        & ~frame["planner_needs_user_input"]
    ].copy()
    if keep.empty:
        return keep
    keep["keep_status"] = "approved_no_change"
    keep["keep_reason"] = keep["planner_reason"]
    return keep.reset_index(drop=True)



def _build_review_queue(frame: pd.DataFrame, executable_manifest: pd.DataFrame, keep_register: pd.DataFrame, blocked_manifest: pd.DataFrame) -> pd.DataFrame:
    excluded = set(executable_manifest.get("relative_path", pd.Series(dtype=object)).astype(str))
    excluded |= set(keep_register.get("relative_path", pd.Series(dtype=object)).astype(str))
    review = frame[~frame["relative_path"].astype(str).isin(excluded)].copy()
    if not blocked_manifest.empty:
        blocked_paths = set(blocked_manifest["relative_path"].astype(str))
        review.loc[review["relative_path"].astype(str).isin(blocked_paths), "planner_action"] = "blocked_review"
        review.loc[review["relative_path"].astype(str).isin(blocked_paths), "planner_reason"] = blocked_manifest.set_index(blocked_manifest["relative_path"].astype(str))["execution_block_reason"].reindex(review["relative_path"].astype(str)).values
    review["review_bucket"] = review["planner_action"].fillna("manual_review")
    return review.reset_index(drop=True)



def _build_rollback_manifest(executable_manifest: pd.DataFrame) -> pd.DataFrame:
    if executable_manifest.empty:
        return executable_manifest.copy()
    rollback = executable_manifest.copy()
    rollback["rollback_operation_id"] = rollback["execution_operation_id"]
    rollback["rollback_action"] = "move_back"
    rollback["rollback_status"] = "pending"
    rollback["rollback_source_relative_path"] = rollback["execution_target_relative_path"]
    rollback["rollback_target_relative_path"] = rollback["execution_source_relative_path"]
    rollback["rollback_source_full_path"] = rollback["execution_target_full_path"]
    rollback["rollback_target_full_path"] = rollback["execution_source_full_path"]
    return rollback.reset_index(drop=True)



def build_execution_bundle(plan_df: pd.DataFrame, config: ManifestConfig | None = None) -> ManifestBundle:
    config = config or ManifestConfig()
    frame = normalize_plan_schema(plan_df)
    executable_manifest, blocked_manifest = _build_executable_manifest(frame, config)
    keep_register = _build_keep_register(frame, config)
    review_queue = _build_review_queue(frame, executable_manifest, keep_register, blocked_manifest)
    rollback_manifest = _build_rollback_manifest(executable_manifest)
    return ManifestBundle(
        executable_manifest=executable_manifest,
        keep_register=keep_register,
        review_queue=review_queue,
        blocked_manifest=blocked_manifest,
        rollback_manifest=rollback_manifest,
    )



def save_manifest_bundle(bundle: ManifestBundle, output_dir: str | Path, stem: str) -> dict[str, tuple[Path, Path]]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    def _save(df: pd.DataFrame, name: str) -> tuple[Path, Path]:
        csv_path = output_path / f"{name}_{stem}.csv"
        parquet_path = output_path / f"{name}_{stem}.parquet"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        df.to_parquet(parquet_path, index=False)
        return csv_path, parquet_path

    return {
        "executable_manifest": _save(bundle.executable_manifest, "execution_manifest_ready"),
        "keep_register": _save(bundle.keep_register, "execution_manifest_keep"),
        "review_queue": _save(bundle.review_queue, "execution_manifest_review"),
        "blocked_manifest": _save(bundle.blocked_manifest, "execution_manifest_blocked"),
        "rollback_manifest": _save(bundle.rollback_manifest, "rollback_manifest"),
    }



def manifest_summary(bundle: ManifestBundle) -> dict[str, int]:
    return {
        "executable_rows": int(len(bundle.executable_manifest)),
        "keep_rows": int(len(bundle.keep_register)),
        "review_rows": int(len(bundle.review_queue)),
        "blocked_rows": int(len(bundle.blocked_manifest)),
        "rollback_rows": int(len(bundle.rollback_manifest)),
    }
