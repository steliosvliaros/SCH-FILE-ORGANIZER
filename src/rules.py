from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import pandas as pd

from .policy_loader import PolicyLoader


@dataclass(frozen=True)
class RuleConfig:
    path_warning_chars: int = 220
    filename_warning_chars: int = 110


def _extract_archive_conditions(policy: dict[str, Any]) -> tuple[set[str], set[str]]:
    junk_exts: set[str] = set()
    junk_names: set[str] = set()
    for rule in policy.get("archive_rules", []):
        if "if_extension_in" in rule:
            junk_exts.update(str(x).lower() for x in rule["if_extension_in"])
        if "if_filename_in" in rule:
            junk_names.update(str(x) for x in rule["if_filename_in"])
    return junk_exts, junk_names


def _canonical_duplicate_flags(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    if "hash" not in df.columns:
        return pd.Series(False, index=df.index)
    ordered = df.sort_values(["hash", "modified_at", "relative_path"], ascending=[True, False, True])
    keep_idx = ordered.groupby("hash", dropna=False).head(1).index
    flags = pd.Series(False, index=df.index)
    dup_mask = df["is_duplicate_hash"].fillna(False) if "is_duplicate_hash" in df.columns else False
    flags.loc[dup_mask] = True
    flags.loc[keep_idx] = False
    return flags


def _ensure_required_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "relative_path" not in out.columns and "absolute_path" in out.columns:
        out["relative_path"] = out["absolute_path"].astype(str)

    if "filename" not in out.columns:
        out["filename"] = out["relative_path"].astype(str).map(lambda x: PurePosixPath(x.replace('\\', '/')).name)
    else:
        missing_filename = out["filename"].isna() | (out["filename"].astype(str).str.strip() == "")
        out.loc[missing_filename, "filename"] = out.loc[missing_filename, "relative_path"].astype(str).map(
            lambda x: PurePosixPath(x.replace('\\', '/')).name
        )

    if "suffix" not in out.columns:
        out["suffix"] = out["filename"].astype(str).map(lambda x: PurePosixPath(x).suffix.lower())
    else:
        missing_suffix = out["suffix"].isna() | (out["suffix"].astype(str).str.strip() == "")
        out.loc[missing_suffix, "suffix"] = out.loc[missing_suffix, "filename"].astype(str).map(
            lambda x: PurePosixPath(x).suffix.lower()
        )

    if "stem" not in out.columns:
        out["stem"] = out["filename"].astype(str).map(lambda x: PurePosixPath(x).stem)

    if "parent_relative" not in out.columns:
        out["parent_relative"] = out["relative_path"].astype(str).map(
            lambda x: str(PurePosixPath(x.replace('\\', '/')).parent).replace('.', '')
        )
        out["parent_relative"] = out["parent_relative"].replace({"": "", ".": ""})

    if "path_length" not in out.columns:
        base_col = "absolute_path" if "absolute_path" in out.columns else "relative_path"
        out["path_length"] = out[base_col].astype(str).str.len()

    if "filename_length" not in out.columns:
        out["filename_length"] = out["filename"].astype(str).str.len()

    if "modified_at" not in out.columns:
        out["modified_at"] = pd.NaT
    out["modified_at"] = pd.to_datetime(out["modified_at"], errors="coerce")

    if "is_duplicate_hash" not in out.columns:
        if "hash" in out.columns:
            dup_sizes = out.groupby("hash")["hash"].transform("size")
            out["is_duplicate_hash"] = dup_sizes > 1
        else:
            out["is_duplicate_hash"] = False

    return out


def classify_inventory(df: pd.DataFrame, policy_loader: PolicyLoader, config: RuleConfig | None = None) -> pd.DataFrame:
    config = config or RuleConfig()
    out = _ensure_required_columns(df)
    if out.empty:
        return out

    filename_parser = policy_loader.compile_filename_regex()
    junk_exts, junk_names = _extract_archive_conditions(policy_loader.policy)
    default_status = "review"

    out["suffix"] = out["suffix"].fillna("").astype(str).str.lower()
    out["filename"] = out["filename"].fillna("").astype(str)
    out["parent_relative"] = out["parent_relative"].fillna("").astype(str)

    out["is_junk_extension"] = out["suffix"].isin(junk_exts)
    out["is_junk_filename"] = out["filename"].isin(junk_names)
    out["is_canonical_duplicate"] = _canonical_duplicate_flags(out)
    out["path_risk"] = out["path_length"] >= config.path_warning_chars
    out["filename_risk"] = out["filename_length"] >= config.filename_warning_chars

    parsed = out["filename"].apply(lambda name: filename_parser.match(name).groupdict() if filename_parser.match(name) else None)
    out["is_policy_compliant_name"] = parsed.notna()
    out["parsed_typeid"] = parsed.apply(lambda x: x.get("typeid") if isinstance(x, dict) else None)
    out["parsed_phase"] = parsed.apply(lambda x: x.get("phase") if isinstance(x, dict) else None)
    out["parsed_doc_type"] = parsed.apply(lambda x: x.get("doc_type") if isinstance(x, dict) else None)
    out["parsed_description"] = parsed.apply(lambda x: x.get("description") if isinstance(x, dict) else None)
    out["parsed_date"] = parsed.apply(lambda x: x.get("date") if isinstance(x, dict) else None)
    out["parsed_version"] = parsed.apply(lambda x: x.get("version") if isinstance(x, dict) else None)
    out["parsed_status"] = parsed.apply(lambda x: x.get("status") if isinstance(x, dict) else None)
    out["parsed_ext"] = parsed.apply(lambda x: x.get("ext") if isinstance(x, dict) else None)

    def build_default_subpath(row: pd.Series) -> str | None:
        phase = row.get("parsed_phase")
        doc_type = row.get("parsed_doc_type")
        if not phase or not doc_type:
            return None
        return policy_loader.default_folder_for(phase, doc_type)

    out["default_folder_subpath"] = out.apply(build_default_subpath, axis=1)

    def build_special_target(row: pd.Series) -> str | None:
        if row.get("is_junk_extension") or row.get("is_junk_filename"):
            return "_DEPRECATED"
        if row.get("is_canonical_duplicate"):
            return "_DUPLICATED"
        if row.get("parsed_status") == "SUPERSEDED":
            return "_SUPERSEDED"
        return None

    out["special_folder_target"] = out.apply(build_special_target, axis=1)

    def classify_row(row: pd.Series) -> tuple[str, str, float]:
        reasons: list[str] = []
        if row["is_junk_extension"]:
            reasons.append("junk extension from archive_rules")
        if row["is_junk_filename"]:
            reasons.append("junk filename from archive_rules")
        if row["is_canonical_duplicate"]:
            reasons.append("duplicate hash non-canonical copy")
        if row["parsed_status"] == "SUPERSEDED":
            reasons.append("status is SUPERSEDED")
        if row["path_risk"]:
            reasons.append("path length warning")
        if row["filename_risk"]:
            reasons.append("filename length warning")

        if row["is_junk_extension"] or row["is_junk_filename"]:
            return "archive_or_delete_candidate", "; ".join(reasons), 0.99
        if row["is_canonical_duplicate"]:
            return "move_to_special_folder", "; ".join(reasons), 0.98
        if row["parsed_status"] == "SUPERSEDED":
            return "move_to_special_folder", "; ".join(reasons), 0.97
        if row["is_policy_compliant_name"]:
            return "compliant_keep_review_path", "; ".join(reasons) or "policy-compliant filename", 0.95
        return default_status, "; ".join(reasons) or "needs classification or rename mapping", 0.50

    classified = out.apply(classify_row, axis=1, result_type="expand")
    out[["rule_status", "rule_reason", "rule_confidence"]] = classified

    def build_relative_target(row: pd.Series) -> str | None:
        filename = row["filename"]
        if row["rule_status"] == "move_to_special_folder":
            special = row["special_folder_target"] or "_REVIEW"
            parent = row.get("parent_relative") or ""
            lifecycle = row["default_folder_subpath"] if row.get("default_folder_subpath") else parent
            base = PurePosixPath(special)
            if lifecycle:
                base = base / lifecycle
            return str(base / filename)
        if row["rule_status"] == "archive_or_delete_candidate":
            return str(PurePosixPath("_DEPRECATED") / filename)
        if row["rule_status"] == "compliant_keep_review_path" and row.get("default_folder_subpath"):
            return str(PurePosixPath(row["default_folder_subpath"]) / filename)
        return None

    out["proposed_relative_target"] = out.apply(build_relative_target, axis=1)

    def action_priority(status: str) -> int:
        order = {
            "archive_or_delete_candidate": 1,
            "move_to_special_folder": 2,
            "compliant_keep_review_path": 3,
            "review": 4,
        }
        return order.get(status, 99)

    out["action_priority"] = out["rule_status"].map(action_priority)
    out = out.sort_values(["action_priority", "relative_path"]).reset_index(drop=True)
    return out


def save_rule_outputs(df: pd.DataFrame, output_base: str | PurePosixPath) -> tuple[str, str]:
    output_base = str(output_base)
    csv_path = f"{output_base}.csv"
    parquet_path = f"{output_base}.parquet"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_parquet(parquet_path, index=False)
    return csv_path, parquet_path
