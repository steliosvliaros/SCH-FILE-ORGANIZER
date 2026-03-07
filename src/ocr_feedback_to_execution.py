from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

import pandas as pd

from src.feedback_to_execution import (
    PromotionConfig,
    build_promoted_plan,
    merge_existing_plan,
    promotion_summary,
)


@dataclass(frozen=True)
class OCRPromotionConfig(PromotionConfig):
    promotion_source_label: str = 'ocr_feedback_rerun'
    merge_with_existing_plan: bool = True


def build_ocr_promoted_plan(ocr_feedback_rerun_df: pd.DataFrame, config: Optional[OCRPromotionConfig] = None) -> pd.DataFrame:
    config = config or OCRPromotionConfig()
    promoted = build_promoted_plan(ocr_feedback_rerun_df, config=config)
    if promoted.empty:
        return promoted
    promoted = promoted.copy()
    promoted['promotion_source'] = config.promotion_source_label
    promoted['promotion_reason'] = promoted['promotion_reason'].astype(str).str.replace(
        'feedback_rerun', config.promotion_source_label, regex=False
    )
    notes = promoted['promotion_notes'].fillna('').astype(str)
    prefix = f'source={config.promotion_source_label}'
    promoted['promotion_notes'] = notes.where(notes.eq(''), prefix + '; ' + notes)
    promoted.loc[notes.eq(''), 'promotion_notes'] = prefix
    return promoted


def merge_ocr_into_plan(existing_plan_df: Optional[pd.DataFrame], ocr_promoted_plan_df: pd.DataFrame) -> pd.DataFrame:
    return merge_existing_plan(existing_plan_df, ocr_promoted_plan_df)


def ocr_promotion_summary(df: pd.DataFrame) -> Dict[str, Any]:
    summary = promotion_summary(df)
    summary['ocr_source_rows'] = int(df['promotion_source'].fillna('').astype(str).eq('ocr_feedback_rerun').sum()) if df is not None and not df.empty and 'promotion_source' in df.columns else 0
    return summary
