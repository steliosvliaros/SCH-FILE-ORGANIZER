from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd

from .inventory import ensure_inventory_schema
from .policy_loader import PolicyLoader
from .reporting import build_review_frame


@dataclass(frozen=True)
class PlanConfig:
    company_folder: str | None = None
    asset_folder: str | None = None
    root_mode: str = "ASSETS"  # ASSETS or ARCHIVE
    archive_year: int | None = None
    enable_superseded_folder: bool = False
    enable_duplicate_folder: bool = False
    enable_deprecated_folder: bool = False
    max_ready_confidence: float = 0.90


SPECIAL_FOLDER_POLICY_NOTES = {
    "_SUPERSEDED": "v2_4 mentions a _SUPERSEDED option but does not define placement/structure; keep as review unless you explicitly enable a local convention.",
    "_DUPLICATED": "v2_4 marks duplicate exact hash for review, not automatic relocation; keep as review unless you explicitly enable a local convention.",
    "_DEPRECATED": "v2_4 does not define a concrete _DEPRECATED location; treat junk as archive/delete review, not auto-move.",
}


def _safe_series(df: pd.DataFrame, name: str, default: Any = None) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([default] * len(df), index=df.index)


def _normalize_review_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = ensure_inventory_schema(df)
    if "rule_status" not in out.columns:
        out["rule_status"] = "review"
    if "rule_reason" not in out.columns:
        out["rule_reason"] = "rule output missing"
    if "rule_confidence" not in out.columns:
        out["rule_confidence"] = 0.0
    if "default_folder_subpath" not in out.columns:
        out["default_folder_subpath"] = pd.NA
    if "proposed_relative_target" not in out.columns:
        out["proposed_relative_target"] = pd.NA
    if "special_folder_target" not in out.columns:
        out["special_folder_target"] = pd.NA
    if "parsed_status" not in out.columns:
        out["parsed_status"] = pd.NA
    return out


def _asset_root_string(policy_loader: PolicyLoader, config: PlanConfig) -> str:
    company = config.company_folder or "{COMPANY_FOLDER}"
    asset = config.asset_folder or "{ASSET_FOLDER}"
    mode = str(config.root_mode).upper()
    if mode == "ARCHIVE":
        year = config.archive_year if config.archive_year is not None else "{YEAR}"
        return str(policy_loader.build_asset_root(company, asset, archive_year=year))
    return str(policy_loader.build_asset_root(company, asset, archive_year=None))


def _current_folder_matches_target(parent_relative: str, target_subpath: str | None) -> bool:
    if not target_subpath:
        return False
    current = PurePosixPath(str(parent_relative or "").replace("\\", "/"))
    target = PurePosixPath(str(target_subpath or "").replace("\\", "/"))
    return current == target


def _join_target(root: str | None, subpath: str | None, filename: str) -> tuple[str | None, str | None]:
    if subpath:
        target_relative = str(PurePosixPath(subpath) / filename)
    else:
        target_relative = None
    if root and target_relative:
        return target_relative, str(PurePosixPath(root) / target_relative)
    return target_relative, None


def build_plan(
    review_df: pd.DataFrame,
    policy_loader: PolicyLoader,
    config: PlanConfig | None = None,
) -> pd.DataFrame:
    config = config or PlanConfig()
    frame = _normalize_review_frame(review_df)
    if frame.empty:
        return frame.copy()

    asset_root = _asset_root_string(policy_loader, config)
    out = frame.copy()

    out["planner_asset_root"] = asset_root
    out["planner_target_subpath"] = pd.NA
    out["planner_target_relative_path"] = pd.NA
    out["planner_target_full_path"] = pd.NA
    out["planner_action"] = "manual_review"
    out["planner_reason"] = _safe_series(out, "rule_reason", "needs manual review")
    out["planner_confidence"] = pd.to_numeric(_safe_series(out, "rule_confidence", 0.0), errors="coerce").fillna(0.0)
    out["planner_ready"] = False
    out["planner_needs_user_input"] = True
    out["planner_root_mode"] = str(config.root_mode).upper()

    for idx, row in out.iterrows():
        filename = str(row.get("filename") or "")
        parent_relative = str(row.get("parent_relative") or "")
        rule_status = str(row.get("rule_status") or "review")
        rule_reason = str(row.get("rule_reason") or "")
        default_subpath = row.get("default_folder_subpath")
        default_subpath = None if pd.isna(default_subpath) else str(default_subpath)
        special_folder = row.get("special_folder_target")
        special_folder = None if pd.isna(special_folder) else str(special_folder)

        action = "manual_review"
        reason = rule_reason or "needs manual review"
        confidence = float(row.get("rule_confidence") or 0.0)
        target_subpath: str | None = None
        target_relative: str | None = None
        target_full: str | None = None
        ready = False
        needs_user_input = True

        if rule_status == "compliant_keep_review_path" and default_subpath:
            target_subpath = default_subpath
            target_relative, target_full = _join_target(asset_root, target_subpath, filename)
            if _current_folder_matches_target(parent_relative, target_subpath):
                action = "keep_in_place"
                reason = f"policy-compliant filename already sits under mapped folder {default_subpath}"
            else:
                action = "move_to_policy_folder"
                reason = f"policy-compliant filename maps to folder {default_subpath}"
            ready = True
            needs_user_input = False
        elif rule_status == "archive_or_delete_candidate":
            action = "archive_or_delete_review"
            reason = f"{rule_reason}; v2_4 says archive_or_delete but does not define a concrete target folder"
            ready = False
            needs_user_input = True
        elif rule_status == "move_to_special_folder":
            if special_folder == "_SUPERSEDED" and config.enable_superseded_folder:
                current_parent = parent_relative or (default_subpath or "")
                target_subpath = str(PurePosixPath("_SUPERSEDED") / current_parent) if current_parent else "_SUPERSEDED"
                target_relative, target_full = _join_target(asset_root, target_subpath, filename)
                action = "move_to_superseded_folder"
                reason = f"status is SUPERSEDED; using explicit local superseded-folder convention under {target_subpath}"
                ready = True
                needs_user_input = False
                confidence = min(confidence, 0.92)
            elif special_folder == "_DUPLICATED" and config.enable_duplicate_folder:
                current_parent = parent_relative or (default_subpath or "")
                target_subpath = str(PurePosixPath("_DUPLICATED") / current_parent) if current_parent else "_DUPLICATED"
                target_relative, target_full = _join_target(asset_root, target_subpath, filename)
                action = "move_to_duplicate_folder"
                reason = f"duplicate exact-hash non-canonical copy; using explicit local duplicate-folder convention under {target_subpath}"
                ready = True
                needs_user_input = False
                confidence = min(confidence, 0.90)
            elif special_folder == "_DEPRECATED" and config.enable_deprecated_folder:
                target_subpath = "_DEPRECATED"
                target_relative, target_full = _join_target(asset_root, target_subpath, filename)
                action = "move_to_deprecated_folder"
                reason = "junk candidate; using explicit local deprecated-folder convention"
                ready = True
                needs_user_input = False
                confidence = min(confidence, 0.88)
            else:
                action = "review_special_folder_policy"
                note = SPECIAL_FOLDER_POLICY_NOTES.get(special_folder or "", "special-folder routing not enabled")
                reason = f"{rule_reason}; {note}"
                ready = False
                needs_user_input = True
        elif rule_status == "review":
            action = "manual_review"
            reason = rule_reason or "needs classification or rename mapping"
            ready = False
            needs_user_input = True
        else:
            action = "manual_review"
            reason = rule_reason or f"unhandled rule status: {rule_status}"
            ready = False
            needs_user_input = True

        out.at[idx, "planner_action"] = action
        out.at[idx, "planner_reason"] = reason
        out.at[idx, "planner_confidence"] = confidence
        out.at[idx, "planner_target_subpath"] = target_subpath
        out.at[idx, "planner_target_relative_path"] = target_relative
        out.at[idx, "planner_target_full_path"] = target_full
        out.at[idx, "planner_ready"] = ready
        out.at[idx, "planner_needs_user_input"] = needs_user_input

    out["planner_would_change_path"] = (
        out["planner_target_relative_path"].fillna("").astype(str)
        != out["relative_path"].fillna("").astype(str)
    ) & out["planner_target_relative_path"].notna()

    priority_map = {
        "move_to_policy_folder": 1,
        "keep_in_place": 2,
        "move_to_superseded_folder": 3,
        "move_to_duplicate_folder": 4,
        "move_to_deprecated_folder": 5,
        "archive_or_delete_review": 6,
        "review_special_folder_policy": 7,
        "manual_review": 8,
    }
    out["planner_priority"] = out["planner_action"].map(priority_map).fillna(99).astype(int)
    out = out.sort_values(["planner_priority", "relative_path"]).reset_index(drop=True)
    return out


def build_plan_from_sources(
    inventory_df: pd.DataFrame | None,
    classification_df: pd.DataFrame | None,
    text_df: pd.DataFrame | None,
    policy_loader: PolicyLoader,
    config: PlanConfig | None = None,
) -> pd.DataFrame:
    review = build_review_frame(inventory_df=inventory_df, classification_df=classification_df, text_df=text_df)
    return build_plan(review, policy_loader=policy_loader, config=config)


def save_plan_outputs(df: pd.DataFrame, output_base: str | Path) -> tuple[Path, Path]:
    output_base = Path(output_base)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_base.with_suffix(".csv")
    parquet_path = output_base.with_suffix(".parquet")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_parquet(parquet_path, index=False)
    return csv_path, parquet_path


def plan_summary(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "ready_rows": 0,
            "needs_user_input": 0,
            "would_change_path": 0,
            "manual_review": 0,
        }
    return {
        "rows": int(len(df)),
        "ready_rows": int(pd.to_numeric(df.get("planner_ready", False), errors="coerce").fillna(False).astype(bool).sum()),
        "needs_user_input": int(pd.to_numeric(df.get("planner_needs_user_input", True), errors="coerce").fillna(True).astype(bool).sum()),
        "would_change_path": int(pd.to_numeric(df.get("planner_would_change_path", False), errors="coerce").fillna(False).astype(bool).sum()),
        "manual_review": int(df.get("planner_action", pd.Series(dtype=object)).eq("manual_review").sum()),
    }
