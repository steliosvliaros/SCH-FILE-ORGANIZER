from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import Any, Dict, Optional

import pandas as pd


def _safe_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value).strip()


@dataclass
class OCRBridgeConfig:
    prefer_ocr_text: bool = True
    require_success_status: bool = True
    carry_existing_text_when_no_ocr: bool = True
    max_text_chars: int = 12000
    max_preview_chars: int = 8000


OCR_REQUIRED_COLUMNS = [
    'relative_path', 'filename', 'suffix',
    'ocr_status', 'ocr_error', 'ocr_text', 'ocr_text_preview',
    'ocr_source', 'ocr_backend', 'ocr_pages_attempted',
    'text_after_ocr', 'text_preview_after_ocr', 'ocr_used_for_text',
]

CANDIDATE_REQUIRED_COLUMNS = [
    'relative_path', 'filename', 'suffix',
    'extracted_text', 'text_preview', 'text_status', 'text_error',
    'canonical_ready', 'unresolved_fields',
]


def normalize_ocr_enriched(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if 'relative_path' not in out.columns:
        out['relative_path'] = ''
    if 'filename' not in out.columns:
        out['filename'] = out['relative_path'].fillna('').astype(str).map(lambda p: Path(p).name)
    if 'suffix' not in out.columns:
        out['suffix'] = out['filename'].fillna('').astype(str).map(lambda p: Path(p).suffix.lower().lstrip('.'))
    for col in OCR_REQUIRED_COLUMNS:
        if col not in out.columns:
            if col == 'ocr_pages_attempted':
                out[col] = 0
            elif col == 'ocr_used_for_text':
                out[col] = False
            else:
                out[col] = ''
    out['ocr_pages_attempted'] = pd.to_numeric(out['ocr_pages_attempted'], errors='coerce').fillna(0).astype(int)
    out['ocr_used_for_text'] = out['ocr_used_for_text'].fillna(False).astype(bool)
    return out


def normalize_candidates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if 'relative_path' not in out.columns:
        out['relative_path'] = ''
    if 'filename' not in out.columns:
        out['filename'] = out['relative_path'].fillna('').astype(str).map(lambda p: Path(p).name)
    if 'suffix' not in out.columns:
        out['suffix'] = out['filename'].fillna('').astype(str).map(lambda p: Path(p).suffix.lower().lstrip('.'))
    for col in CANDIDATE_REQUIRED_COLUMNS:
        if col not in out.columns:
            if col == 'canonical_ready':
                out[col] = False
            else:
                out[col] = ''
    return out


def build_ocr_augmented_candidates(
    candidates_df: pd.DataFrame,
    ocr_df: pd.DataFrame,
    config: Optional[OCRBridgeConfig] = None,
) -> pd.DataFrame:
    config = config or OCRBridgeConfig()
    candidates = normalize_candidates(candidates_df)
    ocr = normalize_ocr_enriched(ocr_df)

    keep_cols = [c for c in OCR_REQUIRED_COLUMNS if c in ocr.columns]
    merged = candidates.merge(
        ocr[keep_cols],
        on=['relative_path', 'filename', 'suffix'],
        how='left',
        suffixes=('', '_ocr'),
    )

    for col in OCR_REQUIRED_COLUMNS:
        if col not in merged.columns:
            if col == 'ocr_pages_attempted':
                merged[col] = 0
            elif col == 'ocr_used_for_text':
                merged[col] = False
            else:
                merged[col] = ''

    merged['ocr_bridge_applied'] = False
    merged['bridge_text_source'] = 'existing_text'
    merged['bridge_text_status'] = merged.get('text_status', '').fillna('')
    merged['bridge_text_error'] = merged.get('text_error', '').fillna('')

    source_text = merged.get('extracted_text', '').fillna('').astype(str)
    ocr_status = merged.get('ocr_status', '').fillna('').astype(str)
    ocr_text = merged.get('text_after_ocr', '').fillna('').astype(str)
    ocr_preview = merged.get('text_preview_after_ocr', '').fillna('').astype(str)

    usable_mask = ocr_text.ne('')
    if config.require_success_status:
        usable_mask = usable_mask & ocr_status.eq('success')

    if config.prefer_ocr_text:
        merged.loc[usable_mask, 'extracted_text'] = ocr_text.loc[usable_mask].str[: config.max_text_chars]
        merged.loc[usable_mask, 'text_preview'] = ocr_preview.loc[usable_mask].str[: config.max_preview_chars]
        merged.loc[usable_mask, 'bridge_text_source'] = 'ocr_enriched_text'
        merged.loc[usable_mask, 'bridge_text_status'] = 'ocr_enriched'
        merged.loc[usable_mask, 'bridge_text_error'] = ''
        merged.loc[usable_mask, 'ocr_bridge_applied'] = True
    elif config.carry_existing_text_when_no_ocr:
        fill_mask = source_text.eq('') & usable_mask
        merged.loc[fill_mask, 'extracted_text'] = ocr_text.loc[fill_mask].str[: config.max_text_chars]
        merged.loc[fill_mask, 'text_preview'] = ocr_preview.loc[fill_mask].str[: config.max_preview_chars]
        merged.loc[fill_mask, 'bridge_text_source'] = 'ocr_fill_empty'
        merged.loc[fill_mask, 'bridge_text_status'] = 'ocr_enriched'
        merged.loc[fill_mask, 'bridge_text_error'] = ''
        merged.loc[fill_mask, 'ocr_bridge_applied'] = True

    return merged


def ocr_bridge_summary(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {'rows': 0, 'ocr_rows_present': 0, 'ocr_bridge_applied_rows': 0, 'success_ocr_rows': 0}
    return {
        'rows': int(len(df)),
        'ocr_rows_present': int(df['ocr_status'].fillna('').astype(str).ne('').sum()) if 'ocr_status' in df.columns else 0,
        'ocr_bridge_applied_rows': int(df['ocr_bridge_applied'].fillna(False).astype(bool).sum()) if 'ocr_bridge_applied' in df.columns else 0,
        'success_ocr_rows': int(df['ocr_status'].fillna('').astype(str).eq('success').sum()) if 'ocr_status' in df.columns else 0,
    }
