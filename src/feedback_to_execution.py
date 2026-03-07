from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Optional

import pandas as pd


PROMOTION_PLAN_COLUMNS = [
    'relative_path', 'filename', 'absolute_path',
    'planner_action', 'planner_reason', 'planner_confidence',
    'planner_ready', 'planner_needs_user_input',
    'planner_target_subpath', 'planner_target_relative_path', 'planner_target_full_path',
    'planner_would_change_path', 'planner_asset_root', 'planner_root_mode',
    'promotion_source', 'promotion_reason', 'promotion_notes',
    'canonical_ready_after', 'became_canonical_ready', 'accepted_field_count', 'accepted_fields',
    'canonical_relative_path_after', 'canonical_reason_after', 'canonical_notes_after', 'length_actions_after',
]


@dataclass(frozen=True)
class PromotionConfig:
    promote_only_newly_ready: bool = True
    require_changed_or_keep_decision: bool = True
    keep_ready_same_path_rows: bool = True
    base_confidence_newly_ready: float = 0.96
    base_confidence_already_ready: float = 0.90
    default_full_root: Optional[str] = None



def _safe_str(value: Any) -> str:
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except Exception:
        pass
    return str(value).strip()



def _derive_parent(path_like: str) -> str:
    text = _safe_str(path_like).replace('\\', '/')
    if not text:
        return ''
    parent = str(PurePosixPath(text).parent)
    return '' if parent in {'', '.'} else parent



def _normalize_bool_series(series: pd.Series, default: bool = False) -> pd.Series:
    if series is None:
        return pd.Series(dtype=bool)
    return series.fillna(default).astype(bool)



def normalize_feedback_rerun(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    defaults: Dict[str, Any] = {
        'relative_path': '',
        'filename': '',
        'absolute_path': pd.NA,
        'canonical_ready_before': False,
        'canonical_ready_after': False,
        'became_canonical_ready': False,
        'accepted_field_count': 0,
        'accepted_fields': '',
        'canonical_relative_path_before': '',
        'canonical_relative_path_after': '',
        'canonical_reason_before': '',
        'canonical_reason_after': '',
        'unresolved_fields_before': '',
        'unresolved_fields_after': '',
        'canonical_notes_after': '',
        'length_actions_after': '',
        'canonical_phase_after': '',
        'canonical_doc_type_after': '',
        'canonical_date_after': '',
        'canonical_version_after': '',
        'canonical_status_after': '',
        'canonical_description_after': '',
    }
    for col, default in defaults.items():
        if col not in out.columns:
            out[col] = default
    if 'filename' not in out.columns or out['filename'].astype(str).eq('').all():
        out['filename'] = out['relative_path'].fillna('').astype(str).map(lambda p: Path(p).name)
    out['accepted_field_count'] = pd.to_numeric(out['accepted_field_count'], errors='coerce').fillna(0).astype(int)
    out['canonical_ready_before'] = _normalize_bool_series(out['canonical_ready_before'])
    out['canonical_ready_after'] = _normalize_bool_series(out['canonical_ready_after'])
    out['became_canonical_ready'] = _normalize_bool_series(out['became_canonical_ready'])
    return out



def build_promoted_plan(feedback_rerun_df: pd.DataFrame, config: Optional[PromotionConfig] = None) -> pd.DataFrame:
    config = config or PromotionConfig()
    frame = normalize_feedback_rerun(feedback_rerun_df)

    rows = []
    for rec in frame.to_dict(orient='records'):
        relative_path = _safe_str(rec.get('relative_path'))
        filename = _safe_str(rec.get('filename')) or Path(relative_path).name
        current_path = relative_path.replace('\\', '/')
        target_path = _safe_str(rec.get('canonical_relative_path_after')).replace('\\', '/')
        ready_after = bool(rec.get('canonical_ready_after'))
        became_ready = bool(rec.get('became_canonical_ready'))
        accepted_count = int(rec.get('accepted_field_count') or 0)

        should_promote = ready_after and ((became_ready if config.promote_only_newly_ready else True) or accepted_count > 0)
        same_path = bool(current_path and target_path and current_path == target_path)

        planner_action = 'manual_review'
        planner_reason = _safe_str(rec.get('canonical_reason_after')) or 'feedback rerun did not produce a promotable canonical target'
        planner_confidence = 0.0
        planner_ready = False
        planner_needs_user_input = True
        planner_target_relative_path: Any = pd.NA
        planner_target_full_path: Any = pd.NA
        planner_would_change_path = False
        promotion_reason = 'not_promoted'
        promotion_notes = _safe_str(rec.get('canonical_notes_after'))

        if should_promote and target_path:
            planner_confidence = config.base_confidence_newly_ready if became_ready else config.base_confidence_already_ready
            planner_ready = True
            planner_needs_user_input = False
            planner_target_relative_path = target_path
            planner_target_subpath = _derive_parent(target_path)
            planner_would_change_path = not same_path
            promotion_reason = 'promoted_from_feedback_rerun'

            if same_path:
                if config.keep_ready_same_path_rows:
                    planner_action = 'keep_in_place'
                    planner_reason = 'canonical path matches current path after feedback rerun'
                else:
                    planner_action = 'manual_review'
                    planner_reason = 'canonical path matches current path; keep rows disabled in promotion config'
                    planner_ready = False
                    planner_needs_user_input = True
            else:
                planner_action = 'move_to_policy_folder'
                planner_reason = 'canonical path resolved after accepted suggestions and deterministic rerun'

            if config.default_full_root:
                planner_target_full_path = str(Path(config.default_full_root) / Path(target_path))
            else:
                planner_target_full_path = pd.NA
        else:
            planner_target_subpath = ''
            if ready_after and not became_ready and config.promote_only_newly_ready:
                planner_reason = 'row is canonical-ready after rerun but promotion is limited to newly ready rows'
            elif ready_after and not target_path:
                planner_reason = 'row is canonical-ready but canonical target path is missing'
            elif not ready_after:
                planner_reason = _safe_str(rec.get('canonical_reason_after')) or 'row remains unresolved after feedback rerun'

        root_mode = 'ARCHIVE' if '/ARCHIVE/' in target_path else 'ACTIVE'
        planner_asset_root = ''
        if target_path:
            parts = [p for p in PurePosixPath(target_path).parts if p not in {'', '.'}]
            if len(parts) >= 3:
                planner_asset_root = '/'.join(parts[:3])

        rows.append({
            'relative_path': relative_path,
            'filename': filename,
            'absolute_path': rec.get('absolute_path', pd.NA),
            'planner_action': planner_action,
            'planner_reason': planner_reason,
            'planner_confidence': planner_confidence,
            'planner_ready': planner_ready,
            'planner_needs_user_input': planner_needs_user_input,
            'planner_target_subpath': planner_target_subpath,
            'planner_target_relative_path': planner_target_relative_path,
            'planner_target_full_path': planner_target_full_path,
            'planner_would_change_path': planner_would_change_path,
            'planner_asset_root': planner_asset_root,
            'planner_root_mode': root_mode,
            'promotion_source': 'feedback_rerun',
            'promotion_reason': promotion_reason,
            'promotion_notes': promotion_notes,
            'canonical_ready_after': ready_after,
            'became_canonical_ready': became_ready,
            'accepted_field_count': accepted_count,
            'accepted_fields': _safe_str(rec.get('accepted_fields')),
            'canonical_relative_path_after': target_path,
            'canonical_reason_after': _safe_str(rec.get('canonical_reason_after')),
            'canonical_notes_after': _safe_str(rec.get('canonical_notes_after')),
            'length_actions_after': _safe_str(rec.get('length_actions_after')),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=PROMOTION_PLAN_COLUMNS)
    for col in PROMOTION_PLAN_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out



def merge_existing_plan(existing_plan_df: Optional[pd.DataFrame], promoted_plan_df: pd.DataFrame) -> pd.DataFrame:
    promoted = promoted_plan_df.copy()
    if existing_plan_df is None or existing_plan_df.empty:
        return promoted.reset_index(drop=True)

    existing = existing_plan_df.copy()
    if 'relative_path' not in existing.columns:
        return promoted.reset_index(drop=True)

    promoted_paths = set(promoted['relative_path'].fillna('').astype(str))
    base = existing[~existing['relative_path'].fillna('').astype(str).isin(promoted_paths)].copy()
    merged = pd.concat([base, promoted], ignore_index=True, sort=False)
    return merged.reset_index(drop=True)



def promotion_summary(df: pd.DataFrame) -> Dict[str, int]:
    if df is None or df.empty:
        return {
            'rows': 0,
            'move_rows': 0,
            'keep_rows': 0,
            'review_rows': 0,
            'newly_ready_rows': 0,
        }
    return {
        'rows': int(len(df)),
        'move_rows': int(df['planner_action'].fillna('').eq('move_to_policy_folder').sum()),
        'keep_rows': int(df['planner_action'].fillna('').eq('keep_in_place').sum()),
        'review_rows': int(df['planner_action'].fillna('').eq('manual_review').sum()),
        'newly_ready_rows': int(df['became_canonical_ready'].fillna(False).sum()),
    }
