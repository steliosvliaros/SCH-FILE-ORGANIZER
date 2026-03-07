from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .inventory import ensure_inventory_schema


@dataclass(frozen=True)
class ValidationConfig:
    source_base_path: str | Path | None = None
    target_base_path: str | Path | None = None
    live_filesystem_check: bool = True
    use_post_inventory: bool = True


APPLY_DEFAULTS: dict[str, Any] = {
    "execution_operation_id": pd.NA,
    "execution_source_relative_path": pd.NA,
    "execution_target_relative_path": pd.NA,
    "execution_source_full_path": pd.NA,
    "execution_target_full_path": pd.NA,
    "execution_action": "move",
    "apply_status": "unknown",
    "apply_message": pd.NA,
    "apply_dry_run": False,
    "apply_batch_order": pd.NA,
    "exists_source_before": pd.NA,
    "exists_target_before": pd.NA,
    "exists_source_after": pd.NA,
    "exists_target_after": pd.NA,
}

MANIFEST_DEFAULTS: dict[str, Any] = {
    "execution_operation_id": pd.NA,
    "execution_source_relative_path": pd.NA,
    "execution_target_relative_path": pd.NA,
    "execution_source_full_path": pd.NA,
    "execution_target_full_path": pd.NA,
    "planner_action": pd.NA,
    "planner_reason": pd.NA,
    "relative_path": pd.NA,
}


STATUS_EQUIVALENTS = {
    "moved": "moved",
    "success": "moved",
    "applied": "moved",
    "done": "moved",
    "dry_run_ready": "dry_run_ready",
    "dry_run": "dry_run_ready",
    "pending": "pending",
    "skipped_exists": "skipped_exists",
    "blocked": "blocked",
    "error": "error",
    "source_missing": "source_missing",
}


def _normalize_status(value: Any) -> str:
    raw = str(value).strip().lower().replace(" ", "_")
    return STATUS_EQUIVALENTS.get(raw, raw or "unknown")



def _safe_series(df: pd.DataFrame, name: str, default: Any = None) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([default] * len(df), index=df.index)



def normalize_executable_manifest(df: pd.DataFrame | None) -> pd.DataFrame:
    base = ensure_inventory_schema(df)
    if base.empty:
        for col, default in MANIFEST_DEFAULTS.items():
            if col not in base.columns:
                base[col] = pd.Series(dtype="object")
        return base
    for col, default in MANIFEST_DEFAULTS.items():
        if col not in base.columns:
            base[col] = default
    if "relative_path" not in base.columns or base["relative_path"].isna().all():
        base["relative_path"] = _safe_series(base, "execution_source_relative_path", pd.NA)
    return base



def normalize_apply_log(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None:
        base = pd.DataFrame()
    else:
        base = df.copy()
    if base.empty:
        for col, default in APPLY_DEFAULTS.items():
            if col not in base.columns:
                base[col] = pd.Series(dtype="object")
        return base
    for col, default in APPLY_DEFAULTS.items():
        if col not in base.columns:
            base[col] = default
    base["apply_status"] = base["apply_status"].map(_normalize_status)
    if "apply_dry_run" in base.columns:
        base["apply_dry_run"] = base["apply_dry_run"].fillna(False).astype(bool)
    return base



def _join_path(base_path: str | Path | None, relative_path: Any) -> str | None:
    if relative_path is None or pd.isna(relative_path):
        return None
    rel = str(relative_path).strip().replace("\\", "/")
    if not rel:
        return None
    rel = rel.lstrip("/")
    if base_path is None:
        return None
    return str((Path(base_path) / Path(rel)).resolve())



def _path_exists(path_like: Any) -> bool | pd.NA:
    if path_like is None or pd.isna(path_like):
        return pd.NA
    try:
        return Path(str(path_like)).exists()
    except OSError:
        return pd.NA



def _inventory_seen(df: pd.DataFrame | None, candidate_rel: Any, candidate_abs: Any) -> bool:
    if df is None or df.empty:
        return False
    rel = None if candidate_rel is None or pd.isna(candidate_rel) else str(candidate_rel)
    abspath = None if candidate_abs is None or pd.isna(candidate_abs) else str(candidate_abs)
    rel_hit = rel is not None and "relative_path" in df.columns and df["relative_path"].astype(str).eq(rel).any()
    abs_hit = abspath is not None and "absolute_path" in df.columns and df["absolute_path"].astype(str).eq(abspath).any()
    return bool(rel_hit or abs_hit)



def _derive_validation(row: pd.Series) -> tuple[str, str, bool]:
    status = _normalize_status(row.get("apply_status"))
    dry_run = bool(row.get("apply_dry_run", False))
    src_exists = row.get("post_source_exists")
    tgt_exists = row.get("post_target_exists")

    if dry_run or status == "dry_run_ready":
        return "dry_run_not_applied", "row was only simulated; no post-apply move expected", False

    if status == "moved":
        if tgt_exists is True and src_exists is False:
            return "validated_move", "target exists and source no longer exists", True
        if tgt_exists is True and src_exists is True:
            return "validated_with_source_still_present", "target exists but source still exists; review copy vs move behavior", False
        if tgt_exists is False and src_exists is True:
            return "move_not_observed", "source still exists and target missing after reported move", False
        if tgt_exists is False and src_exists is False:
            return "missing_both_after_move", "neither source nor target was found after reported move", False
        return "reported_move_unverified", "reported move could not be verified from current filesystem state", False

    if status in {"skipped_exists", "blocked", "pending", "source_missing", "error", "unknown"}:
        return "not_applied_or_needs_review", f"apply status was {status}", False

    return "unknown_review", f"unhandled apply status {status}", False



def build_post_apply_validation(
    executable_manifest: pd.DataFrame | None,
    apply_log: pd.DataFrame | None,
    config: ValidationConfig | None = None,
    post_inventory_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    config = config or ValidationConfig()
    manifest = normalize_executable_manifest(executable_manifest)
    log = normalize_apply_log(apply_log)

    base = manifest.copy()
    if not log.empty:
        join_keys = [k for k in ["execution_operation_id"] if k in base.columns and k in log.columns]
        if join_keys and base["execution_operation_id"].notna().any() and log["execution_operation_id"].notna().any():
            log_s = log.rename(columns={c: f"{c}_log" for c in log.columns if c not in join_keys})
            base = base.merge(log_s, on=join_keys, how="left")
        else:
            alt_keys = [k for k in ["execution_source_relative_path", "execution_target_relative_path"] if k in base.columns and k in log.columns]
            if alt_keys:
                log_s = log.rename(columns={c: f"{c}_log" for c in log.columns if c not in alt_keys})
                base = base.merge(log_s, on=alt_keys, how="left")

    if "apply_status" not in base.columns and "apply_status_log" in base.columns:
        base["apply_status"] = base["apply_status_log"]
    if "apply_dry_run" not in base.columns and "apply_dry_run_log" in base.columns:
        base["apply_dry_run"] = base["apply_dry_run_log"]
    if "apply_message" not in base.columns and "apply_message_log" in base.columns:
        base["apply_message"] = base["apply_message_log"]

    base["execution_source_full_path_resolved"] = _safe_series(base, "execution_source_full_path", pd.NA)
    source_missing_mask = base["execution_source_full_path_resolved"].isna() | base["execution_source_full_path_resolved"].astype(str).eq("")
    base.loc[source_missing_mask, "execution_source_full_path_resolved"] = base.loc[source_missing_mask, "execution_source_relative_path"].map(lambda x: _join_path(config.source_base_path, x))

    base["execution_target_full_path_resolved"] = _safe_series(base, "execution_target_full_path", pd.NA)
    target_missing_mask = base["execution_target_full_path_resolved"].isna() | base["execution_target_full_path_resolved"].astype(str).eq("")
    base.loc[target_missing_mask, "execution_target_full_path_resolved"] = base.loc[target_missing_mask, "execution_target_relative_path"].map(lambda x: _join_path(config.target_base_path or config.source_base_path, x))

    if config.live_filesystem_check:
        base["post_source_exists"] = base["execution_source_full_path_resolved"].map(_path_exists)
        base["post_target_exists"] = base["execution_target_full_path_resolved"].map(_path_exists)
    else:
        base["post_source_exists"] = pd.NA
        base["post_target_exists"] = pd.NA

    if config.use_post_inventory and post_inventory_df is not None:
        post_inv = ensure_inventory_schema(post_inventory_df)
        base["post_inventory_source_seen"] = [
            _inventory_seen(post_inv, rel, full)
            for rel, full in zip(base.get("execution_source_relative_path", pd.Series(dtype=object)), base.get("execution_source_full_path_resolved", pd.Series(dtype=object)))
        ]
        base["post_inventory_target_seen"] = [
            _inventory_seen(post_inv, rel, full)
            for rel, full in zip(base.get("execution_target_relative_path", pd.Series(dtype=object)), base.get("execution_target_full_path_resolved", pd.Series(dtype=object)))
        ]
    else:
        base["post_inventory_source_seen"] = False
        base["post_inventory_target_seen"] = False

    statuses = []
    reasons = []
    validated = []
    for _, row in base.iterrows():
        s, r, ok = _derive_validation(row)
        statuses.append(s)
        reasons.append(r)
        validated.append(ok)
    base["validation_status"] = statuses
    base["validation_reason"] = reasons
    base["validated_change"] = validated
    base["requires_manual_review"] = ~base["validated_change"]
    return base



def validation_summary(frame: pd.DataFrame) -> dict[str, int]:
    if frame is None or frame.empty:
        return {
            "rows": 0,
            "validated_moves": 0,
            "dry_run_rows": 0,
            "manual_review": 0,
        }
    return {
        "rows": int(len(frame)),
        "validated_moves": int(frame.get("validation_status", pd.Series(dtype=object)).eq("validated_move").sum()),
        "dry_run_rows": int(frame.get("validation_status", pd.Series(dtype=object)).eq("dry_run_not_applied").sum()),
        "manual_review": int(frame.get("requires_manual_review", pd.Series(dtype=bool)).fillna(False).sum()),
    }
