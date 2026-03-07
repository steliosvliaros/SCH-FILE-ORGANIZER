from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
import re
import shutil

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


@dataclass(frozen=True)
class ApplyConfig:
    dry_run: bool = True
    batch_size: int | None = 25
    create_target_parents: bool = True
    overwrite_existing: bool = False
    allow_placeholder_paths: bool = False
    stop_on_error: bool = False
    source_base_path: str | Path | None = None
    target_base_path: str | Path | None = None


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


EXECUTABLE_DEFAULTS: dict[str, Any] = {
    "execution_operation_id": pd.NA,
    "execution_action": "move",
    "execution_status": "pending",
    "execution_blocked": False,
    "execution_block_reason": pd.NA,
    "execution_target_parent": pd.NA,
    "execution_source_relative_path": pd.NA,
    "execution_target_relative_path": pd.NA,
    "execution_source_full_path": pd.NA,
    "execution_target_full_path": pd.NA,
}


PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")


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



def normalize_executable_manifest(df: pd.DataFrame) -> pd.DataFrame:
    out = ensure_inventory_schema(df)
    out = normalize_plan_schema(out)
    for col, default in EXECUTABLE_DEFAULTS.items():
        if col not in out.columns:
            out[col] = default
    if out.empty:
        return out
    out["execution_blocked"] = pd.Series(out["execution_blocked"], index=out.index).fillna(False).astype(bool)
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



def _contains_placeholder(path_like: str | None) -> bool:
    if not path_like:
        return False
    return bool(PLACEHOLDER_RE.search(str(path_like)))



def _build_executable_manifest(frame: pd.DataFrame, config: ManifestConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    action_mask = frame["planner_action"].isin(config.executable_actions)
    ready_mask = frame["planner_ready"] & ~frame["planner_needs_user_input"]
    target_mask = frame["planner_target_relative_path"].notna() if config.require_target_path else True
    changed_mask = frame["planner_would_change_path"] if config.require_changed_path else True

    candidates = frame[action_mask & ready_mask & target_mask & changed_mask].copy()
    if candidates.empty:
        empty = normalize_executable_manifest(frame.iloc[0:0].copy())
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

    placeholder_mask = candidates["execution_target_relative_path"].astype(str).map(_contains_placeholder) | candidates["execution_target_full_path"].astype(str).map(_contains_placeholder)
    if placeholder_mask.any():
        blocked = candidates[placeholder_mask].copy()
        blocked["execution_blocked"] = True
        blocked["execution_block_reason"] = "unresolved_placeholder_in_target_path"
        blocked_rows.append(blocked)
        candidates = candidates[~placeholder_mask].copy()

    blocked_manifest = pd.concat(blocked_rows, ignore_index=True) if blocked_rows else normalize_executable_manifest(candidates.iloc[0:0].copy())
    return normalize_executable_manifest(candidates.reset_index(drop=True)), normalize_executable_manifest(blocked_manifest.reset_index(drop=True))



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
        mapping = blocked_manifest.assign(_rp=blocked_manifest["relative_path"].astype(str)).drop_duplicates("_rp").set_index("_rp")["execution_block_reason"]
        mask = review["relative_path"].astype(str).isin(blocked_paths)
        review.loc[mask, "planner_action"] = "blocked_review"
        review.loc[mask, "planner_reason"] = review.loc[mask, "relative_path"].astype(str).map(mapping)
    review["review_bucket"] = review["planner_action"].fillna("manual_review")
    return review.reset_index(drop=True)



def _build_rollback_manifest(executable_manifest: pd.DataFrame) -> pd.DataFrame:
    executable_manifest = normalize_executable_manifest(executable_manifest)
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



def _resolve_runtime_path(full_path: Any, relative_path: Any, base_path: str | Path | None) -> Path | None:
    if pd.notna(full_path) and str(full_path).strip():
        return Path(str(full_path))
    if base_path is not None and pd.notna(relative_path) and str(relative_path).strip():
        rel = str(relative_path).replace('/', Path().anchor if False else '/')
        return Path(base_path) / Path(str(relative_path))
    return None



def apply_manifest(executable_manifest: pd.DataFrame, config: ApplyConfig | None = None) -> pd.DataFrame:
    config = config or ApplyConfig()
    manifest = normalize_executable_manifest(executable_manifest)
    if manifest.empty:
        return pd.DataFrame(columns=[
            "execution_operation_id",
            "apply_mode",
            "apply_status",
            "apply_reason",
            "apply_timestamp_utc",
            "execution_source_relative_path",
            "execution_target_relative_path",
            "apply_source_path",
            "apply_target_path",
        ])

    if config.batch_size is not None and config.batch_size >= 0:
        manifest = manifest.head(int(config.batch_size)).copy()

    records: list[dict[str, Any]] = []
    for _, row in manifest.iterrows():
        source_path = _resolve_runtime_path(
            row.get("execution_source_full_path"),
            row.get("execution_source_relative_path") or row.get("relative_path"),
            config.source_base_path,
        )
        target_path = _resolve_runtime_path(
            row.get("execution_target_full_path"),
            row.get("execution_target_relative_path"),
            config.target_base_path,
        )

        status = "pending"
        reason = ""
        if bool(row.get("execution_blocked", False)):
            status = "blocked"
            reason = str(row.get("execution_block_reason") or "execution row blocked in manifest")
        elif source_path is None:
            status = "blocked"
            reason = "missing_source_path"
        elif target_path is None:
            status = "blocked"
            reason = "missing_target_path"
        elif (not config.allow_placeholder_paths) and (_contains_placeholder(str(source_path)) or _contains_placeholder(str(target_path))):
            status = "blocked"
            reason = "unresolved_placeholder_in_runtime_path"
        elif not source_path.exists():
            status = "blocked"
            reason = "source_missing_on_disk"
        elif target_path.exists() and not config.overwrite_existing:
            status = "blocked"
            reason = "target_exists"
        else:
            try:
                if config.create_target_parents and target_path is not None:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                if config.dry_run:
                    status = "dry_run_ready"
                    reason = "validated_no_changes_written"
                else:
                    shutil.move(str(source_path), str(target_path))
                    status = "moved"
                    reason = "move_completed"
            except Exception as exc:  # pragma: no cover - defensive runtime path
                status = "error"
                reason = f"{type(exc).__name__}: {exc}"
                if config.stop_on_error:
                    records.append({
                        "execution_operation_id": row.get("execution_operation_id"),
                        "apply_mode": "dry_run" if config.dry_run else "apply",
                        "apply_status": status,
                        "apply_reason": reason,
                        "apply_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "execution_source_relative_path": row.get("execution_source_relative_path") or row.get("relative_path"),
                        "execution_target_relative_path": row.get("execution_target_relative_path"),
                        "apply_source_path": None if source_path is None else str(source_path),
                        "apply_target_path": None if target_path is None else str(target_path),
                    })
                    raise

        records.append({
            "execution_operation_id": row.get("execution_operation_id"),
            "apply_mode": "dry_run" if config.dry_run else "apply",
            "apply_status": status,
            "apply_reason": reason,
            "apply_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "execution_source_relative_path": row.get("execution_source_relative_path") or row.get("relative_path"),
            "execution_target_relative_path": row.get("execution_target_relative_path"),
            "apply_source_path": None if source_path is None else str(source_path),
            "apply_target_path": None if target_path is None else str(target_path),
        })

    return pd.DataFrame.from_records(records)



def apply_summary(log_df: pd.DataFrame) -> dict[str, int]:
    if log_df.empty:
        return {
            "rows": 0,
            "dry_run_ready": 0,
            "moved": 0,
            "blocked": 0,
            "error": 0,
        }
    status_counts = log_df.get("apply_status", pd.Series(dtype=object)).value_counts()
    return {
        "rows": int(len(log_df)),
        "dry_run_ready": int(status_counts.get("dry_run_ready", 0)),
        "moved": int(status_counts.get("moved", 0)),
        "blocked": int(status_counts.get("blocked", 0)),
        "error": int(status_counts.get("error", 0)),
    }



def save_apply_log(log_df: pd.DataFrame, output_dir: str | Path, stem: str) -> tuple[Path, Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    csv_path = output_path / f"apply_log_{stem}.csv"
    parquet_path = output_path / f"apply_log_{stem}.parquet"
    jsonl_path = output_path / f"apply_log_{stem}.jsonl"
    log_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log_df.to_parquet(parquet_path, index=False)
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in log_df.to_dict(orient="records"):
            f.write(pd.Series(row).to_json(force_ascii=False) + "\n")
    return csv_path, parquet_path, jsonl_path
