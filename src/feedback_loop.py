from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from .canonicalize import CanonicalizeConfig, canonicalize_row, normalize_extension


def _safe_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value).strip()


TYPEID_PATTERN = re.compile(r'([A-Z]{2,3}\d{2}p\d{3}-\d{2})')

SUGGESTION_TO_CANONICAL = {
    'suggested_phase': 'phase',
    'suggested_doc_type': 'doc_type',
    'suggested_date': 'date',
    'suggested_version': 'version',
    'suggested_status': 'status',
    'suggested_description': 'description',
    'suggested_company_folder': 'company_folder',
    'suggested_asset_folder': 'asset_folder',
}

CANONICAL_BASE_COLUMNS = [
    'relative_path', 'filename', 'canonical_company_folder', 'canonical_asset_folder',
    'canonical_lifecycle_subpath', 'canonical_filename', 'canonical_relative_path',
    'canonical_phase', 'canonical_doc_type', 'canonical_date', 'canonical_version',
    'canonical_status', 'canonical_description', 'unresolved_fields', 'canonical_notes',
    'canonical_ready', 'length_actions', 'full_path_len', 'filename_len',
    'max_full_path_chars', 'max_filename_chars', 'is_within_limits', 'canonical_reason',
]

SUGGESTION_COLUMNS = [
    'relative_path', 'filename', 'unresolved_fields',
    'suggested_phase', 'suggested_doc_type', 'suggested_date', 'suggested_version',
    'suggested_status', 'suggested_description', 'suggested_description_source',
    'suggested_company_folder', 'suggested_asset_folder', 'suggested_owner_or_project',
    'suggested_location', 'suggested_namecode', 'suggested_typ3', 'suggested_vat9',
    'suggestion_confidence', 'suggestion_source', 'suggestion_reason', 'suggestion_evidence',
]


@dataclass
class FeedbackConfig:
    auto_accept_fields: Sequence[str] = (
        'description',
        'doc_type',
        'phase',
        'date',
        'version',
        'status',
    )
    min_confidence: float = 0.80
    require_content_derived_description: bool = True
    keep_existing_canonical_values: bool = True


def normalize_canonical_candidates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in CANONICAL_BASE_COLUMNS:
        if col not in out.columns:
            out[col] = '' if col not in {'canonical_ready', 'is_within_limits'} else False
    if 'filename' not in out.columns:
        out['filename'] = out['relative_path'].fillna('').astype(str).map(lambda p: Path(p).name)
    return out


def normalize_llm_suggestions(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in SUGGESTION_COLUMNS:
        if col not in out.columns:
            if col == 'suggestion_confidence':
                out[col] = 0.0
            else:
                out[col] = ''
    if 'filename' not in out.columns:
        out['filename'] = out['relative_path'].fillna('').astype(str).map(lambda p: Path(p).name)
    return out


def _field_is_missing(row: Mapping[str, Any], field: str) -> bool:
    canonical_col = f'canonical_{field}' if field not in {'company_folder', 'asset_folder'} else f'canonical_{field}'
    value = _safe_str(row.get(canonical_col))
    if field in {'company_folder', 'asset_folder'}:
        return value == ''
    return value == ''


def _description_source_ok(source: str, require_content_derived: bool) -> bool:
    source = _safe_str(source)
    if not require_content_derived:
        return source != ''
    return source.startswith('content') or source == 'ollama_content_inference'


def build_feedback_review(
    canonical_df: pd.DataFrame,
    suggestions_df: pd.DataFrame,
    config: Optional[FeedbackConfig] = None,
) -> pd.DataFrame:
    config = config or FeedbackConfig()
    canon = normalize_canonical_candidates(canonical_df)
    sugg = normalize_llm_suggestions(suggestions_df)

    merged = canon.merge(
        sugg,
        on=['relative_path', 'filename'],
        how='left',
        suffixes=('', '_sugg'),
    )

    merged['suggestion_confidence'] = pd.to_numeric(merged['suggestion_confidence'], errors='coerce').fillna(0.0)
    merged['accepted_field_count'] = 0

    for suggested_col, field in SUGGESTION_TO_CANONICAL.items():
        accept_col = f'accept_{field}'
        proposed = merged[suggested_col].fillna('').astype(str)
        base_accept = proposed.ne('') & merged['suggestion_confidence'].ge(config.min_confidence)

        if field == 'description':
            base_accept = base_accept & merged['suggested_description_source'].fillna('').astype(str).map(
                lambda s: _description_source_ok(s, config.require_content_derived_description)
            )

        if field not in set(config.auto_accept_fields):
            base_accept = pd.Series(False, index=merged.index)

        if config.keep_existing_canonical_values:
            base_accept = base_accept & merged.apply(lambda r: _field_is_missing(r, field), axis=1)

        unresolved_series = merged['unresolved_fields'].fillna('').astype(str)
        unresolved_match = unresolved_series.map(lambda x: field in {t.strip() for t in x.split(';') if t.strip()})
        if field in {'company_folder', 'asset_folder'}:
            unresolved_match = unresolved_match | merged.apply(lambda r: _field_is_missing(r, field), axis=1)
        base_accept = base_accept & unresolved_match

        merged[accept_col] = base_accept.fillna(False)
        merged['accepted_field_count'] = merged['accepted_field_count'] + merged[accept_col].astype(int)

    merged['has_any_accepted_suggestion'] = merged['accepted_field_count'] > 0
    return merged


def _infer_typeid(row: Mapping[str, Any]) -> str:
    for key in ['canonical_filename', 'filename', 'canonical_asset_folder', 'relative_path']:
        text = _safe_str(row.get(key))
        m = TYPEID_PATTERN.search(text)
        if m:
            token = m.group(1).upper()
            if token.startswith('PV') and not token.startswith('PVS'):
                token = 'PVS' + token[2:]
            return token
    return ''


def _prepare_feedback_input_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        'relative_path': _safe_str(row.get('relative_path')),
        'filename': _safe_str(row.get('filename')),
        'ext': normalize_extension(Path(_safe_str(row.get('filename'))).suffix.lower().lstrip('.')),
        'typeid': _infer_typeid(row),
        'company_folder': _safe_str(row.get('canonical_company_folder')),
        'asset_folder': _safe_str(row.get('canonical_asset_folder')),
        'phase': _safe_str(row.get('canonical_phase')),
        'doc_type': _safe_str(row.get('canonical_doc_type')),
        'date': _safe_str(row.get('canonical_date')),
        'version': _safe_str(row.get('canonical_version')),
        'status': _safe_str(row.get('canonical_status')),
        'description': _safe_str(row.get('canonical_description')),
    }

    for suggested_col, field in SUGGESTION_TO_CANONICAL.items():
        accept_col = f'accept_{field}'
        if bool(row.get(accept_col)):
            out[field] = _safe_str(row.get(suggested_col))

    return out


RESULT_COLUMNS = [
    'relative_path', 'filename', 'canonical_ready_before', 'canonical_ready_after',
    'became_canonical_ready', 'accepted_field_count', 'accepted_fields',
    'canonical_relative_path_before', 'canonical_relative_path_after',
    'canonical_reason_before', 'canonical_reason_after',
    'unresolved_fields_before', 'unresolved_fields_after',
    'canonical_notes_after', 'length_actions_after',
    'canonical_phase_after', 'canonical_doc_type_after', 'canonical_date_after',
    'canonical_version_after', 'canonical_status_after', 'canonical_description_after',
]


def rerun_canonical_with_feedback(
    review_df: pd.DataFrame,
    policy: Mapping[str, Any],
    canonical_config: Optional[CanonicalizeConfig] = None,
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for rec in review_df.to_dict(orient='records'):
        prepared = _prepare_feedback_input_row(rec)
        rerun = canonicalize_row(prepared, policy, config=canonical_config)
        accepted_fields = []
        for _, field in SUGGESTION_TO_CANONICAL.items():
            if bool(rec.get(f'accept_{field}')):
                accepted_fields.append(field)
        rows.append({
            'relative_path': _safe_str(rec.get('relative_path')),
            'filename': _safe_str(rec.get('filename')),
            'canonical_ready_before': bool(rec.get('canonical_ready')),
            'canonical_ready_after': bool(rerun.get('canonical_ready')),
            'became_canonical_ready': (not bool(rec.get('canonical_ready'))) and bool(rerun.get('canonical_ready')),
            'accepted_field_count': len(accepted_fields),
            'accepted_fields': ';'.join(accepted_fields),
            'canonical_relative_path_before': _safe_str(rec.get('canonical_relative_path')),
            'canonical_relative_path_after': _safe_str(rerun.get('canonical_relative_path')),
            'canonical_reason_before': _safe_str(rec.get('canonical_reason')),
            'canonical_reason_after': _safe_str(rerun.get('canonical_reason')),
            'unresolved_fields_before': _safe_str(rec.get('unresolved_fields')),
            'unresolved_fields_after': _safe_str(rerun.get('unresolved_fields')),
            'canonical_notes_after': _safe_str(rerun.get('canonical_notes')),
            'length_actions_after': _safe_str(rerun.get('length_actions')),
            'canonical_phase_after': _safe_str(rerun.get('canonical_phase')),
            'canonical_doc_type_after': _safe_str(rerun.get('canonical_doc_type')),
            'canonical_date_after': _safe_str(rerun.get('canonical_date')),
            'canonical_version_after': _safe_str(rerun.get('canonical_version')),
            'canonical_status_after': _safe_str(rerun.get('canonical_status')),
            'canonical_description_after': _safe_str(rerun.get('canonical_description')),
            **{k: v for k, v in rerun.items() if k not in {
                'relative_path', 'filename', 'canonical_ready', 'canonical_relative_path', 'canonical_reason',
                'unresolved_fields', 'canonical_notes', 'length_actions', 'canonical_phase', 'canonical_doc_type',
                'canonical_date', 'canonical_version', 'canonical_status', 'canonical_description',
            }},
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return out


def feedback_summary(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {
            'rows': 0,
            'rows_with_accepted_suggestions': 0,
            'rows_became_canonical_ready': 0,
            'already_ready_before': 0,
            'ready_after': 0,
        }
    return {
        'rows': int(len(df)),
        'rows_with_accepted_suggestions': int(df['accepted_field_count'].fillna(0).gt(0).sum()),
        'rows_became_canonical_ready': int(df['became_canonical_ready'].fillna(False).sum()),
        'already_ready_before': int(df['canonical_ready_before'].fillna(False).sum()),
        'ready_after': int(df['canonical_ready_after'].fillna(False).sum()),
    }
