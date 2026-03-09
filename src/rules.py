from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import pandas as pd


DATAFRAME_DEFAULTS = {
    "relative_path": "",
    "absolute_path": "",
    "filename": "",
    "stem": "",
    "suffix": "",
    "size_bytes": 0,
    "parent_relative": "",
    "path_length": 0,
    "filename_length": 0,
    "depth_segments": 0,
    "is_hidden": False,
    "is_symlink": False,
    "content_hash": "",
    "hash_blake2b": "",
    "hash_sha256": "",
    "is_duplicate_hash": False,
    "duplicate_group_size": 0,
}


def _as_posix_str(value: Any) -> str:
    return str(value).replace("\\", "/") if value is not None else ""


def ensure_inventory_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Backfill older inventory outputs so later logic can rely on a stable schema."""
    df = df.copy()
    for col, default in DATAFRAME_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default

    if not df["relative_path"].astype(str).str.len().any() and "absolute_path" in df.columns:
        df["relative_path"] = df["absolute_path"].astype(str)

    if "filename" not in df.columns or not df["filename"].astype(str).str.len().all():
        df["filename"] = df["relative_path"].astype(str).map(lambda p: Path(_as_posix_str(p)).name)

    if "stem" not in df.columns or not df["stem"].astype(str).str.len().all():
        df["stem"] = df["filename"].astype(str).map(lambda f: Path(f).stem)

    if "suffix" not in df.columns or not df["suffix"].astype(str).str.len().all():
        df["suffix"] = df["filename"].astype(str).map(lambda f: Path(f).suffix.lower())
    else:
        df["suffix"] = df["suffix"].astype(str).str.lower()

    if "parent_relative" not in df.columns or not df["parent_relative"].astype(str).str.len().all():
        df["parent_relative"] = df["relative_path"].astype(str).map(lambda p: str(Path(_as_posix_str(p)).parent).replace("\\", "/"))

    if "path_length" not in df.columns or (df["path_length"] == 0).all():
        df["path_length"] = df["relative_path"].astype(str).map(len)

    if "filename_length" not in df.columns or (df["filename_length"] == 0).all():
        df["filename_length"] = df["filename"].astype(str).map(len)

    if "depth_segments" not in df.columns or (df["depth_segments"] == 0).all():
        df["depth_segments"] = df["relative_path"].astype(str).map(lambda p: len([s for s in _as_posix_str(p).split("/") if s]))

    # normalize duplicate hash signal from any hash column if not present
    hash_col = _pick_hash_column(df)
    if hash_col and ("is_duplicate_hash" not in df.columns or not df["is_duplicate_hash"].astype(bool).any()):
        counts = df[hash_col].fillna("").astype(str).value_counts()
        dup_vals = set(counts[counts > 1].index) - {""}
        df["is_duplicate_hash"] = df[hash_col].fillna("").astype(str).isin(dup_vals)
        df["duplicate_group_size"] = df[hash_col].fillna("").astype(str).map(lambda v: int(counts.get(v, 0)) if v in dup_vals else 0)

    return df


def _pick_hash_column(df: pd.DataFrame) -> Optional[str]:
    for col in ("content_hash", "hash_blake2b", "hash_sha256"):
        if col in df.columns:
            return col
    return None


def _load_policy(policy: Union[Dict[str, Any], str, Path]) -> Dict[str, Any]:
    if isinstance(policy, dict):
        return policy
    path = Path(policy)
    import yaml  # local import to keep module dependency light

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def normalize_archive_policy(policy: Union[Dict[str, Any], str, Path]) -> Dict[str, Any]:
    raw = _load_policy(policy)
    rules = raw.get("archive_rules", {})

    normalized = {
        "mode": "unknown",
        "junk_extensions": [],
        "junk_filenames": [],
        "duplicate_action": "review",
        "superseded_action": "review",
        "cancelled_assets_action": None,
        "preserve_relative_path": False,
        "duplicate_special_folder": "_DUPLICATED",
        "superseded_special_folder": "_SUPERSEDED",
    }

    if isinstance(rules, list):
        normalized["mode"] = "legacy_list"
        for item in rules:
            if not isinstance(item, dict):
                continue
            if "if_extension_in" in item:
                normalized["junk_extensions"].extend([str(x).lower() for x in item.get("if_extension_in", [])])
                normalized["junk_action"] = item.get("then", "archive_or_delete")
            if "if_filename_in" in item:
                normalized["junk_filenames"].extend([str(x) for x in item.get("if_filename_in", [])])
                normalized["junk_action"] = item.get("then", "archive_or_delete")
            if "if_duplicate_exact_hash" in item:
                normalized["duplicate_action"] = item.get("then", "review")
            if item.get("if_status_is") == "SUPERSEDED":
                normalized["superseded_action"] = item.get("then", "review")
    elif isinstance(rules, dict):
        normalized["mode"] = "structured_dict"
        normalized["junk_extensions"] = [str(x).lower() for x in rules.get("deprecated_or_temp_extensions", [])]
        normalized["junk_filenames"] = [str(x) for x in rules.get("ignore_system_files", [])]
        normalized["duplicate_action"] = str(rules.get("duplicated_hash_action", "review"))
        normalized["superseded_action"] = str(rules.get("superseded_status_action", "review"))
        normalized["cancelled_assets_action"] = rules.get("cancelled_assets")
        normalized["preserve_relative_path"] = "preserve_relative_path" in normalized["duplicate_action"] or "preserve_relative_path" in normalized["superseded_action"]

    special = raw.get("special_storage_policy", {})
    if isinstance(special, dict):
        folders = special.get("special_folders", {})
        if isinstance(folders, dict):
            normalized["duplicate_special_folder"] = str(folders.get("duplicated", normalized["duplicate_special_folder"]))
            normalized["superseded_special_folder"] = str(folders.get("superseded", normalized["superseded_special_folder"]))
        placement = special.get("placement")
        if isinstance(placement, str) and "preserve_relative" in placement:
            normalized["preserve_relative_path"] = True

    return normalized


def normalize_policy_structure(policy: Union[Dict[str, Any], str, Path]) -> Dict[str, Any]:
    raw = _load_policy(policy)
    version = str(raw.get("version", "")).lower()
    style = str(raw.get("policy_style", "")).lower()
    is_v25 = version.startswith("v2_5") or "workstream_first" in style or "routing_rules" in raw

    phase_codes = raw.get("controlled_vocabularies", {}).get("phase_codes", {})
    workstream_codes = raw.get("controlled_vocabularies", {}).get("workstream_codes", {})
    workstream_folder_map = raw.get("routing_rules", {}).get("workstream_folder_map", {})

    # Legacy v2_4/v2_3 fallback based on PHASE_FOLDER_MAP and default_folder per doc type.
    legacy_phase_map = raw.get("folder_buckets", {}).get("PHASE_FOLDER_MAP", {})
    legacy_doc_types = raw.get("document_types", {})

    normalized_doc_routing: Dict[str, Dict[str, Any]] = {}
    if is_v25:
        normalized_doc_routing = raw.get("routing_rules", {}).get("document_type_routing", {})
    else:
        for doc_type, cfg in legacy_doc_types.items():
            if not isinstance(cfg, dict):
                continue
            normalized_doc_routing[doc_type] = {
                "strategy": "legacy_default_folder",
                "fixed_folder": cfg.get("default_folder", ""),
                "filename_template": cfg.get("filename_template"),
            }

    path_limits = raw.get("path_length_limits", {})
    constraints = raw.get("naming_policy", {}).get("constraints", {})

    normalized = {
        "raw": raw,
        "version_mode": "v2_5_like" if is_v25 else "legacy_v2_4_like",
        "phase_codes": phase_codes,
        "workstream_codes": workstream_codes,
        "workstream_folder_map": workstream_folder_map,
        "legacy_phase_folder_map": legacy_phase_map,
        "doc_routing": normalized_doc_routing,
        "status_tags": raw.get("controlled_vocabularies", {}).get("status_tags", []),
        "user_selectable_subtypes": raw.get("controlled_vocabularies", {}).get("user_selectable_subtypes", {}),
        "archive": normalize_archive_policy(raw),
        "company_pattern": raw.get("naming_policy", {}).get("company_folder", {}).get("pattern", "{NAMECODE}-{TYP3}-{VAT9}"),
        "asset_pattern": raw.get("naming_policy", {}).get("asset_folder", {}).get("pattern", "{TYPEID}_{PROJECT_NAME}_{LOCATION}"),
        "filename_template": raw.get("naming_policy", {}).get("filenames", {}).get("internal", {}).get("template", "{TYPEID}_{PHASE}_{DOCTYPE}_{DESCRIPTION}_{DATE}_{VERSION}_{STATUS}.{EXT}"),
        "full_path_limit": int(path_limits.get("hard_limit_full_path", constraints.get("max_full_path_chars", 240))),
        "filename_limit": int(path_limits.get("hard_limit_filename", constraints.get("max_filename_chars", 120))),
        "shortening_order": path_limits.get("shortening_order", []),
    }
    return normalized


def _build_filename_regex(policy_cfg: Dict[str, Any]) -> re.Pattern[str]:
    phase_tokens = list(policy_cfg.get("phase_codes", {}).keys()) or ["FS", "LA", "PM", "DE", "FN", "PR", "CN", "CM", "OP", "DC"]
    doc_tokens = list(policy_cfg.get("doc_routing", {}).keys()) or ["FIN", "CNT", "PER", "COR", "REP", "DRWTEC", "SDYTEC", "MIN", "DAT"]
    status_tokens = list(policy_cfg.get("status_tags", [])) or ["DRAFT", "REVIEW", "REVISED", "FINAL", "APPROVED", "SIGNED", "SENT", "RECEIVED", "SUPERSEDED", "CANCELLED"]

    phase_alt = "|".join(map(re.escape, sorted(phase_tokens, key=len, reverse=True)))
    doc_alt = "|".join(map(re.escape, sorted(doc_tokens, key=len, reverse=True)))
    status_alt = "|".join(map(re.escape, sorted(status_tokens, key=len, reverse=True)))

    # TYPEID supports 2-4 letters in practice to be robust across legacy and current policy.
    pat = rf"^(?P<TYPEID>[A-Z]{{2,4}}\d{{2}}p\d{{3}}-\d{{2}})_(?P<PHASE>{phase_alt})_(?P<DOCTYPE>{doc_alt})_(?P<DESCRIPTION>.+)_(?P<DATE>\d{{8}})_(?P<VERSION>v\d{{2}})_(?P<STATUS>{status_alt})$"
    return re.compile(pat)


def _extract_company_asset_prefix(relative_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    parts = [p for p in _as_posix_str(relative_path).split("/") if p]
    company_idx = None
    asset_idx = None
    company_pat = re.compile(r"^[A-Za-z0-9]+-[A-Z]{3}-\d{9}$")
    asset_pat = re.compile(r"^[A-Z]{2,4}\d{2}p\d{3}-\d{2}_.+_.+$")

    for i, part in enumerate(parts):
        if company_idx is None and company_pat.match(part):
            company_idx = i
        if asset_idx is None and asset_pat.match(part):
            asset_idx = i

    company = parts[company_idx] if company_idx is not None else None
    asset = parts[asset_idx] if asset_idx is not None else None
    base = None
    if company and asset:
        base = f"{company}/ASSETS/{asset}"
    elif asset:
        base = asset
    return company, asset, base


def _detect_current_workstream(relative_path: str, policy_cfg: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    path = _as_posix_str(relative_path)
    workstream_map = policy_cfg.get("workstream_folder_map", {})
    for code, folder in workstream_map.items():
        if f"/{folder}/" in f"/{path}/" or path.endswith(f"/{folder}"):
            return code, folder
    if policy_cfg.get("version_mode") == "legacy_v2_4_like":
        legacy = policy_cfg.get("legacy_phase_folder_map", {})
        for phase, folder in legacy.items():
            if f"/{folder}/" in f"/{path}/" or path.endswith(f"/{folder}"):
                return _phase_to_workstream(phase), folder
    return None, None


def _phase_to_workstream(phase: Optional[str]) -> Optional[str]:
    mapping = {
        "FS": "PC",
        "LA": "LS",
        "PM": "PMT",
        "DE": "TDE",
        "FN": "FNI",
        "PR": "PRC",
        "CN": "CND",
        "CM": "CND",
        "OP": "OPM",
        "DC": "DCM",
    }
    return mapping.get(str(phase or ""))


def _validate_counterparty_description(doc_type: str, description: str) -> Tuple[bool, str]:
    if doc_type not in {"FIN", "CNT"}:
        return True, "not_applicable"
    if not description:
        return False, "missing_description"
    # COUNTERPARTY-rest or COUNTERPARTY-to-OTHER-rest. COUNTERPARTY tokens uppercase.
    if re.match(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*-[a-z0-9].+", description):
        return True, "single_counterparty_rule_ok"
    if re.match(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*-to-[A-Z0-9]+(?:-[A-Z0-9]+)*-[a-z0-9].+", description):
        return True, "two_party_counterparty_rule_ok"
    return False, "fin_cnt_counterparty_naming_not_compliant"


def _resolve_v25_route(doc_type: str, phase: Optional[str], relative_path: str, parsed: Dict[str, Any], policy_cfg: Dict[str, Any]) -> Tuple[str, str, str]:
    routing = policy_cfg.get("doc_routing", {}).get(doc_type, {})
    strategy = routing.get("strategy", "fallback")
    current_workstream_code, _ = _detect_current_workstream(relative_path, policy_cfg)
    inferred_workstream = current_workstream_code or _phase_to_workstream(phase)
    routing_basis = "current_workstream" if current_workstream_code else "phase_to_workstream"

    if strategy == "fixed_folder":
        folder = routing.get("fixed_folder", routing.get("fallback_folder", "99_UNROUTED"))
        return folder, inferred_workstream or "", strategy

    if strategy == "by_workstream":
        folder = routing.get("workstream_routes", {}).get(inferred_workstream, routing.get("fallback_folder", "99_UNROUTED"))
        return folder, inferred_workstream or "", routing_basis

    if strategy in {"by_workstream_then_optional_payment_category", "by_workstream_then_optional_contract_category"}:
        folder = routing.get("workstream_routes", {}).get(inferred_workstream, routing.get("fallback_folder", "99_UNROUTED"))
        return folder, inferred_workstream or "", routing_basis

    if strategy == "by_deliverable_stage":
        stage = parsed.get("DELIVERABLE_STAGE") or parsed.get("deliverable_stage")
        routes = routing.get("routes", {})
        folder = routes.get(stage, routing.get("fallback_folder", "99_UNROUTED"))
        return folder, inferred_workstream or "", f"deliverable_stage:{stage or 'fallback'}"

    return routing.get("fallback_folder", routing.get("fixed_folder", "99_UNROUTED")), inferred_workstream or "", "fallback"


def _resolve_legacy_route(doc_type: str, phase: Optional[str], policy_cfg: Dict[str, Any]) -> Tuple[str, str, str]:
    routing = policy_cfg.get("doc_routing", {}).get(doc_type, {})
    default_folder = routing.get("fixed_folder") or routing.get("default_folder") or ""
    if default_folder:
        return default_folder, _phase_to_workstream(phase) or "", "legacy_default_folder"
    phase_folder = policy_cfg.get("legacy_phase_folder_map", {}).get(phase or "", "")
    if doc_type == "REP" and phase_folder:
        return f"{phase_folder}/Reports", _phase_to_workstream(phase) or "", "legacy_phase_reports"
    return phase_folder, _phase_to_workstream(phase) or "", "legacy_phase_folder"


def _build_proposed_relative_target(relative_path: str, route_subpath: str, filename: str) -> str:
    company, asset, base = _extract_company_asset_prefix(relative_path)
    if base:
        return f"{base}/{route_subpath}/{filename}".replace("//", "/")
    # If the scan root is already the asset folder, keep target relative to that root.
    if asset:
        return f"{asset}/{route_subpath}/{filename}".replace("//", "/")
    return f"{route_subpath}/{filename}".replace("//", "/")


def _current_route_subpath(relative_path: str) -> str:
    company, asset, _ = _extract_company_asset_prefix(relative_path)
    parts = [p for p in _as_posix_str(relative_path).split("/") if p]
    if company and asset:
        try:
            company_i = parts.index(company)
            asset_i = parts.index(asset)
            tail = parts[asset_i + 1 : -1]
            return "/".join(tail)
        except ValueError:
            pass
    return "/".join(parts[:-1])


def _parse_filename(stem: str, policy_cfg: Dict[str, Any]) -> Dict[str, Any]:
    regex = _build_filename_regex(policy_cfg)
    m = regex.match(stem)
    if not m:
        return {"parsed_ok": False}
    data = m.groupdict()
    data["parsed_ok"] = True
    return data


def classify_inventory(inv: pd.DataFrame, policy: Union[Dict[str, Any], str, Path]) -> pd.DataFrame:
    """Classify files in a backward-compatible way while understanding v2.5 routing.

    Keeps the broad downstream contract stable:
      - rule_status
      - rule_reason
      - rule_confidence
      - proposed_relative_target

    Adds extra metadata columns for richer policy awareness without breaking later stages.
    """
    df = ensure_inventory_schema(inv)
    policy_cfg = normalize_policy_structure(policy)
    archive_cfg = policy_cfg["archive"]

    out = df.copy()

    # Stable output columns expected downstream.
    out["rule_status"] = "review"
    out["rule_reason"] = "unclassified"
    out["rule_confidence"] = "low"
    out["proposed_relative_target"] = ""
    out["special_folder_target"] = ""

    # Extra metadata columns (safe additions).
    out["policy_version_mode"] = policy_cfg["version_mode"]
    out["archive_policy_mode"] = archive_cfg["mode"]
    out["parsed_typeid"] = ""
    out["parsed_phase"] = ""
    out["parsed_doc_type"] = ""
    out["parsed_description"] = ""
    out["parsed_date"] = ""
    out["parsed_version"] = ""
    out["parsed_status"] = ""
    out["detected_workstream"] = ""
    out["detected_subfolder"] = ""
    out["routing_basis"] = ""
    out["counterparty_rule_ok"] = False
    out["counterparty_rule_reason"] = "not_applicable"
    out["default_folder_subpath"] = ""
    out["current_folder_subpath"] = out["relative_path"].astype(str).map(_current_route_subpath)
    out["path_length_warning"] = out["path_length"] > policy_cfg["full_path_limit"]
    out["filename_length_warning"] = out["filename_length"] > policy_cfg["filename_limit"]
    # Backward-compatible legacy review columns expected by older notebooks.
    out["path_risk"] = out["path_length_warning"].map(lambda v: "high" if bool(v) else "")
    out["filename_risk"] = out["filename_length_warning"].map(lambda v: "high" if bool(v) else "")

    junk_exts = set(archive_cfg.get("junk_extensions", []))
    junk_names = set(archive_cfg.get("junk_filenames", []))

    for idx, row in out.iterrows():
        filename = str(row.get("filename", ""))
        stem = str(row.get("stem", ""))
        suffix = str(row.get("suffix", "")).lower()
        rel = _as_posix_str(row.get("relative_path", ""))

        # 1) hard junk/system file checks
        if filename in junk_names or suffix in junk_exts:
            out.at[idx, "rule_status"] = "archive_or_delete_candidate"
            out.at[idx, "rule_reason"] = "junk_system_or_temp_file"
            out.at[idx, "rule_confidence"] = "high"
            continue

        # 2) duplicate handling
        if bool(row.get("is_duplicate_hash", False)):
            dup_action = archive_cfg.get("duplicate_action", "review")
            if "move" in str(dup_action).lower() or "duplicated" in str(dup_action).lower():
                out.at[idx, "rule_status"] = "move_to_special_folder"
                out.at[idx, "rule_reason"] = "duplicate_exact_hash"
                out.at[idx, "special_folder_target"] = archive_cfg.get("duplicate_special_folder", "_DUPLICATED")
            else:
                out.at[idx, "rule_status"] = "review"
                out.at[idx, "rule_reason"] = "duplicate_exact_hash_review"
            out.at[idx, "rule_confidence"] = "high"
            continue

        parsed = _parse_filename(stem, policy_cfg)
        if parsed.get("parsed_ok"):
            out.at[idx, "parsed_typeid"] = parsed.get("TYPEID", "")
            out.at[idx, "parsed_phase"] = parsed.get("PHASE", "")
            out.at[idx, "parsed_doc_type"] = parsed.get("DOCTYPE", "")
            out.at[idx, "parsed_description"] = parsed.get("DESCRIPTION", "")
            out.at[idx, "parsed_date"] = parsed.get("DATE", "")
            out.at[idx, "parsed_version"] = parsed.get("VERSION", "")
            out.at[idx, "parsed_status"] = parsed.get("STATUS", "")

            # 3) special status handling
            if parsed.get("STATUS") == "SUPERSEDED":
                superseded_action = archive_cfg.get("superseded_action", "review")
                if "move" in str(superseded_action).lower() or "superceded" in str(superseded_action).lower() or "_SUPERSEDED" in str(superseded_action):
                    out.at[idx, "rule_status"] = "move_to_special_folder"
                    out.at[idx, "special_folder_target"] = archive_cfg.get("superseded_special_folder", "_SUPERSEDED")
                else:
                    out.at[idx, "rule_status"] = "review"
                out.at[idx, "rule_reason"] = "superseded_status"
                out.at[idx, "rule_confidence"] = "high"
                continue

            # 4) validate v2.5 FIN/CNT counterparty naming rule without breaking later flow
            counterparty_ok, counterparty_reason = _validate_counterparty_description(parsed.get("DOCTYPE", ""), parsed.get("DESCRIPTION", ""))
            out.at[idx, "counterparty_rule_ok"] = bool(counterparty_ok)
            out.at[idx, "counterparty_rule_reason"] = counterparty_reason

            # 5) route resolution by policy version
            if policy_cfg["version_mode"] == "v2_5_like":
                route_subpath, workstream_code, routing_basis = _resolve_v25_route(parsed.get("DOCTYPE", ""), parsed.get("PHASE", ""), rel, parsed, policy_cfg)
            else:
                route_subpath, workstream_code, routing_basis = _resolve_legacy_route(parsed.get("DOCTYPE", ""), parsed.get("PHASE", ""), policy_cfg)

            out.at[idx, "detected_workstream"] = workstream_code or ""
            out.at[idx, "detected_subfolder"] = route_subpath or ""
            out.at[idx, "default_folder_subpath"] = route_subpath or ""
            out.at[idx, "routing_basis"] = routing_basis
            proposed_target = _build_proposed_relative_target(rel, route_subpath, filename) if route_subpath else ""
            out.at[idx, "proposed_relative_target"] = proposed_target

            current_subpath = _current_route_subpath(rel)
            folder_match = bool(route_subpath) and current_subpath.endswith(route_subpath)

            if counterparty_ok and folder_match:
                out.at[idx, "rule_status"] = "compliant_keep_review_path"
                out.at[idx, "rule_reason"] = "policy_compliant_name_and_folder"
                out.at[idx, "rule_confidence"] = "high"
            elif not counterparty_ok and parsed.get("DOCTYPE", "") in {"FIN", "CNT"}:
                out.at[idx, "rule_status"] = "review"
                out.at[idx, "rule_reason"] = counterparty_reason
                out.at[idx, "rule_confidence"] = "medium"
            else:
                out.at[idx, "rule_status"] = "review"
                out.at[idx, "rule_reason"] = "parsed_filename_noncanonical_folder_or_needs_reroute"
                out.at[idx, "rule_confidence"] = "medium"
        else:
            # keep non-parsing rows in review; later canonical/LLM steps will help
            out.at[idx, "rule_status"] = "review"
            out.at[idx, "rule_reason"] = "filename_not_in_canonical_pattern"
            out.at[idx, "rule_confidence"] = "low"

    mask_move = out["rule_status"].astype(str).eq("move_to_special_folder") & out["special_folder_target"].astype(str).eq("")
    if mask_move.any():
        dup_mask = mask_move & out["rule_reason"].astype(str).str.contains("duplicate", case=False, na=False)
        sup_mask = mask_move & out["rule_reason"].astype(str).str.contains("superseded", case=False, na=False)
        out.loc[dup_mask, "special_folder_target"] = archive_cfg.get("duplicate_special_folder", "_DUPLICATED")
        out.loc[sup_mask, "special_folder_target"] = archive_cfg.get("superseded_special_folder", "_SUPERSEDED")

    priority_map = {
        'archive_or_delete_candidate': 10,
        'move_to_special_folder': 20,
        'review': 30,
        'compliant_keep_review_path': 40,
    }
    
    out['action_priority'] = out['rule_status'].map(priority_map).fillna(99).astype(int)

    return out


def save_rule_outputs(classified: pd.DataFrame, output_dir: Union[str, Path], stem: Optional[str] = None) -> Tuple[Path, Path]:
    """Save rule classification outputs as CSV and Parquet.

    Keeps the legacy notebook contract stable.
    """
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    if stem is None:
        stem = f"rule_classification_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    csv_path = outdir / f"{stem}.csv"
    parquet_path = outdir / f"{stem}.parquet"
    classified.to_csv(csv_path, index=False, encoding='utf-8-sig')
    try:
        classified.to_parquet(parquet_path, index=False)
    except Exception:
        # keep CSV as the minimum guaranteed export if parquet engine is unavailable
        parquet_path = outdir / f"{stem}.parquet"
    return csv_path, parquet_path


__all__ = [
    "ensure_inventory_schema",
    "normalize_archive_policy",
    "normalize_policy_structure",
    "classify_inventory",
    "save_rule_outputs",
]
