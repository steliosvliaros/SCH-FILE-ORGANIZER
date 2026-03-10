from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
import json
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


@dataclass(frozen=True, init=False)
class ApplyConfig:
    dry_run: bool
    batch_limit: int | None
    allow_live_apply: bool
    create_target_parent_dirs: bool
    block_unresolved_placeholders: bool
    move_mode: str
    overwrite_existing: bool
    source_base_path: str | None
    target_base_path: str | None

    def __init__(
        self,
        dry_run: bool = True,
        batch_limit: int | None = 20,
        allow_live_apply: bool = False,
        create_target_parent_dirs: bool = True,
        block_unresolved_placeholders: bool = True,
        move_mode: str = "shutil_move",
        overwrite_existing: bool = False,
        source_base_path: str | None = None,
        target_base_path: str | None = None,
        batch_size: int | None = None,
        create_target_parents: bool | None = None,
        **kwargs: Any,
    ) -> None:
        if batch_size is not None:
            batch_limit = batch_size
        if create_target_parents is not None:
            create_target_parent_dirs = create_target_parents
        if "create_target_parent_dirs" in kwargs and create_target_parents is None:
            create_target_parent_dirs = kwargs.pop("create_target_parent_dirs")
        if "batch_limit" in kwargs and batch_size is None:
            batch_limit = kwargs.pop("batch_limit")
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected ApplyConfig argument(s): {unexpected}")
        object.__setattr__(self, "dry_run", bool(dry_run))
        object.__setattr__(self, "batch_limit", batch_limit)
        object.__setattr__(self, "allow_live_apply", bool(allow_live_apply))
        object.__setattr__(self, "create_target_parent_dirs", bool(create_target_parent_dirs))
        object.__setattr__(self, "block_unresolved_placeholders", bool(block_unresolved_placeholders))
        object.__setattr__(self, "move_mode", str(move_mode))
        object.__setattr__(self, "overwrite_existing", bool(overwrite_existing))
        object.__setattr__(self, "source_base_path", None if source_base_path is None else str(source_base_path))
        object.__setattr__(self, "target_base_path", None if target_base_path is None else str(target_base_path))


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

APPLY_DEFAULTS: dict[str, Any] = {
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
    for col, default in APPLY_DEFAULTS.items():
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


def _build_executable_manifest(frame: pd.DataFrame, config: ManifestConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    action_mask = frame["planner_action"].isin(config.executable_actions)
    ready_mask = frame["planner_ready"] & ~frame["planner_needs_user_input"]
    target_mask = frame["planner_target_relative_path"].notna() if config.require_target_path else True
    changed_mask = frame["planner_would_change_path"] if config.require_changed_path else True

    candidates = frame[action_mask & ready_mask & target_mask & changed_mask].copy()
    if candidates.empty:
        empty = frame.iloc[0:0].copy()
        for col in APPLY_DEFAULTS:
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


def _resolve_full_path(base_path: str | None, relative_path: Any) -> str | None:
    if relative_path is None or pd.isna(relative_path):
        return None
    rel = str(relative_path)
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute() or base_path is None:
        return str(p)
    return str(Path(base_path) / Path(rel))


def _has_placeholder(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    s = str(value)
    return "{" in s and "}" in s


def apply_manifest(executable_manifest: pd.DataFrame, config: ApplyConfig | None = None) -> pd.DataFrame:
    config = config or ApplyConfig()
    frame = normalize_executable_manifest(executable_manifest).copy()
    if frame.empty:
        for col in ["apply_status", "apply_message", "apply_dry_run", "apply_timestamp"]:
            if col not in frame.columns:
                frame[col] = pd.Series(dtype=object)
        return frame

    if config.batch_limit is not None:
        frame = frame.head(config.batch_limit).copy()

    results: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        source = row.get("execution_source_full_path")
        target = row.get("execution_target_full_path")
        blocked_reason = row.get("execution_block_reason")
        status = "pending"
        message = ""

        if bool(row.get("execution_blocked", False)):
            status = "blocked"
            message = str(blocked_reason or "execution blocked")
        elif _has_placeholder(target) and config.block_unresolved_placeholders:
            status = "blocked"
            message = "target path contains unresolved placeholder"
        else:
            if not source or pd.isna(source):
                source = _resolve_full_path(config.source_base_path, row.get("execution_source_relative_path") or row.get("relative_path"))
            if not target or pd.isna(target):
                target = _resolve_full_path(config.target_base_path, row.get("execution_target_relative_path") or row.get("planner_target_relative_path"))

            if not source or pd.isna(source):
                status = "error"
                message = "missing execution_source_full_path"
            elif not target or pd.isna(target):
                status = "error"
                message = "missing execution_target_full_path"
            elif config.dry_run or not config.allow_live_apply:
                status = "dry_run_ready"
                message = "validated for dry run; no filesystem changes applied"
            else:
                src = Path(str(source))
                dst = Path(str(target))
                try:
                    if dst.exists() and not config.overwrite_existing:
                        status = "blocked"
                        message = "target already exists and overwrite_existing is False"
                    else:
                        if config.create_target_parent_dirs:
                            dst.parent.mkdir(parents=True, exist_ok=True)
                        if dst.exists() and config.overwrite_existing:
                            if dst.is_dir():
                                shutil.rmtree(dst)
                            else:
                                dst.unlink()
                        shutil.move(str(src), str(dst))
                        status = "moved"
                        message = "move completed"
                except Exception as exc:
                    status = "error"
                    message = f"move failed: {exc}"

        out = row.to_dict()
        out["apply_status"] = status
        out["apply_message"] = message
        out["apply_dry_run"] = bool(config.dry_run or not config.allow_live_apply)
        out["apply_timestamp"] = pd.Timestamp.utcnow().isoformat()
        results.append(out)

    return pd.DataFrame(results)


def apply_summary(apply_log: pd.DataFrame) -> dict[str, int]:
    if apply_log.empty or "apply_status" not in apply_log.columns:
        return {"rows": int(len(apply_log)), "moved": 0, "dry_run_ready": 0, "blocked": 0, "error": 0}
    counts = apply_log["apply_status"].fillna("unknown").value_counts().to_dict()
    return {
        "rows": int(len(apply_log)),
        "moved": int(counts.get("moved", 0)),
        "dry_run_ready": int(counts.get("dry_run_ready", 0)),
        "blocked": int(counts.get("blocked", 0)),
        "error": int(counts.get("error", 0)),
    }


def save_apply_log(apply_log: pd.DataFrame, output_dir: str | Path, stem: str) -> dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"apply_log_{stem}.csv"
    parquet_path = out_dir / f"apply_log_{stem}.parquet"
    jsonl_path = out_dir / f"apply_log_{stem}.jsonl"
    apply_log.to_csv(csv_path, index=False, encoding="utf-8-sig")
    apply_log.to_parquet(parquet_path, index=False)
    with jsonl_path.open("w", encoding="utf-8") as f:
        for record in apply_log.to_dict(orient="records"):
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return {"csv": csv_path, "parquet": parquet_path, "jsonl": jsonl_path}


def _first_existing_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _series_to_absolute_path(series: pd.Series, base_path: str | None) -> pd.Series:
    def _to_absolute(value: Any) -> str | None:
        if value is None or pd.isna(value):
            return None
        raw = str(value).strip()
        if not raw:
            return None
        candidate = Path(raw)
        if candidate.is_absolute() or base_path is None:
            return str(candidate)
        return str(Path(base_path) / candidate)

    return series.map(_to_absolute)


def build_copy_manifest_with_clean_root(
    csv_path: str | Path,
    source_root: str | Path = r"C:\SONADO-IK-801455240",
    source_segment: str = "HTL0049-01_OITYLO-KOKKALA_MANI",
    target_segment: str = "HTL0049-01_OITYLO-KOKKALA_MANI_CLEAN",
) -> pd.DataFrame:
    frame = pd.read_csv(csv_path)

    current_col = _first_existing_column(
        frame,
        (
            "absolute_current_path",
            "execution_source_full_path",
            "absolute_path",
            "execution_source_relative_path",
            "relative_path",
        ),
    )
    proposed_col = _first_existing_column(
        frame,
        (
            "absolute_proposed_path",
            "execution_target_full_path",
            "planner_target_full_path",
            "execution_target_relative_path",
            "planner_target_relative_path",
        ),
    )

    if current_col is None:
        raise ValueError("Could not derive current path column from CSV")
    if proposed_col is None:
        raise ValueError("Could not derive proposed path column from CSV")

    out = frame.copy()
    source_root_text = str(source_root)
    out["absolute_current_path"] = _series_to_absolute_path(out[current_col], source_root_text)
    out["absolute_proposed_path"] = _series_to_absolute_path(out[proposed_col], source_root_text)
    out["absolute_proposed_path"] = out["absolute_proposed_path"].map(
        lambda p: None if p is None else str(p).replace(source_segment, target_segment)
    )
    return out


def copy_and_rename_from_paths(copy_manifest: pd.DataFrame, overwrite_existing: bool = False) -> pd.DataFrame:
    if copy_manifest.empty:
        return pd.DataFrame(columns=["absolute_current_path", "absolute_proposed_path", "copy_status", "copy_message"])

    results: list[dict[str, Any]] = []
    for _, row in copy_manifest.iterrows():
        current = row.get("absolute_current_path")
        proposed = row.get("absolute_proposed_path")
        status = "pending"
        message = ""

        if not current or pd.isna(current):
            status = "error"
            message = "missing absolute_current_path"
        elif not proposed or pd.isna(proposed):
            status = "error"
            message = "missing absolute_proposed_path"
        else:
            src = Path(str(current))
            dst = Path(str(proposed))
            try:
                if not src.exists() or not src.is_file():
                    status = "missing_source"
                    message = "source file does not exist"
                elif dst.exists() and not overwrite_existing:
                    status = "blocked"
                    message = "target already exists and overwrite_existing is False"
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    status = "copied"
                    message = "copy and rename completed"
            except Exception as exc:
                status = "error"
                message = f"copy failed: {exc}"

        out = row.to_dict()
        out["copy_status"] = status
        out["copy_message"] = message
        out["copy_timestamp"] = pd.Timestamp.utcnow().isoformat()
        results.append(out)

    return pd.DataFrame(results)


def process_rule_classification_copy(
    csv_path: str | Path,
    source_root: str | Path = r"C:\SONADO-IK-801455240",
    source_segment: str = "HTL0049-01_OITYLO-KOKKALA_MANI",
    target_segment: str = "HTL0049-01_OITYLO-KOKKALA_MANI_CLEAN",
    overwrite_existing: bool = False,
) -> pd.DataFrame:
    manifest = build_copy_manifest_with_clean_root(
        csv_path=csv_path,
        source_root=source_root,
        source_segment=source_segment,
        target_segment=target_segment,
    )
    return copy_and_rename_from_paths(manifest, overwrite_existing=overwrite_existing)
