from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
import pandas as pd


@dataclass(frozen=True)
class StageSpec:
    stage: str
    notebook: str
    output_patterns: tuple[str, ...]
    branch: str = 'core'
    optional: bool = False
    description: str = ''


STAGES: list[StageSpec] = [
    StageSpec('01_policy_check', '01_policy_check.ipynb', tuple(), 'core', False, 'Validate YAML policy and vocabularies.'),
    StageSpec('02_inventory', '02_inventory.ipynb', ('inventory_*.parquet',), 'core', False, 'Scan files and write inventory parquet/csv.'),
    StageSpec('03_rule_classification', '03_rule_classification.ipynb', ('rule_classification_*.parquet',), 'core', False, 'Apply deterministic rules to inventory.'),
    StageSpec('04_extract_text', '04_extract_text.ipynb', ('inventory_with_text_*.parquet',), 'core', False, 'Extract text previews from supported file types.'),
    StageSpec('05_review_outputs', '05_review_outputs.ipynb', ('review_snapshot_latest.parquet',), 'core', False, 'Merge outputs into one review frame.'),
    StageSpec('06_planner', '06_planner.ipynb', ('plan_dry_run_*.parquet',), 'core', False, 'Build conservative dry-run plan.'),
    StageSpec('07_execution_manifest', '07_execution_manifest.ipynb', ('execution_manifest_ready_*.parquet', 'rollback_manifest_*.parquet'), 'core', False, 'Split plan into executable, keep, review, blocked, rollback.'),
    StageSpec('08_apply_changes', '08_apply_changes.ipynb', ('apply_log_*.parquet',), 'ops', True, 'Apply executable manifest in dry-run or small live batch.'),
    StageSpec('09_rollback', '09_rollback.ipynb', ('rollback_log_*.parquet',), 'ops', True, 'Rollback rows that were actually moved.'),
    StageSpec('10_post_apply_validation', '10_post_apply_validation.ipynb', ('post_apply_validation_*.parquet',), 'ops', True, 'Validate planned vs applied vs observed-on-disk state.'),
    StageSpec('11_canonical_rename_engine', '11_canonical_rename_engine.ipynb', ('canonical_candidates_*.parquet',), 'quality', False, 'Deterministic canonical path/name engine.'),
    StageSpec('12_llm_suggestions', '12_llm_suggestions.ipynb', ('llm_suggestions_*.parquet', 'canonical_with_suggestions_*.parquet'), 'quality', True, 'Suggest missing fields only for unresolved rows.'),
    StageSpec('13_feedback_loop', '13_feedback_loop.ipynb', ('canonical_feedback_rerun_*.parquet',), 'quality', True, 'Feed accepted suggestions back into deterministic canonicalization.'),
    StageSpec('14_feedback_to_execution', '14_feedback_to_execution.ipynb', ('plan_promoted_from_feedback_*.parquet', 'plan_with_feedback_*.parquet'), 'quality', True, 'Promote newly canonical-ready rows back into planning/execution.'),
    StageSpec('15_ocr_layer', '15_ocr_layer.ipynb', ('ocr_enriched_*.parquet',), 'ocr', True, 'OCR for scanned PDFs and image-only files.'),
    StageSpec('16_ocr_feedback_loop', '16_ocr_feedback_loop.ipynb', ('ocr_canonical_feedback_rerun_*.parquet',), 'ocr', True, 'Use OCR text to improve suggestions and rerun canonicalization.'),
    StageSpec('17_ocr_feedback_to_execution', '17_ocr_feedback_to_execution.ipynb', ('plan_promoted_from_ocr_feedback_*.parquet', 'plan_with_ocr_feedback_*.parquet'), 'ocr', True, 'Promote OCR-rescued rows back into planning/execution.'),
]


def find_project_root(start: Path) -> Path:
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / 'src').exists() and (candidate / 'notebooks').exists():
            return candidate
    return start


def _latest_match(directory: Path, pattern: str) -> Optional[Path]:
    candidates = [p for p in directory.glob(pattern) if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def build_stage_status(outputs_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    for spec in STAGES:
        matches = []
        existing = []
        for pattern in spec.output_patterns:
            p = _latest_match(outputs_dir, pattern)
            matches.append(str(p) if p else None)
            existing.append(bool(p))
        latest_file = next((m for m in matches if m), None)
        available = all(existing) if spec.output_patterns else True
        rows.append({
            'stage': spec.stage,
            'branch': spec.branch,
            'optional': spec.optional,
            'notebook': spec.notebook,
            'description': spec.description,
            'output_patterns': '; '.join(spec.output_patterns),
            'latest_file': latest_file,
            'available': available,
        })
    return pd.DataFrame(rows)


def build_run_plan(outputs_dir: Path, include_ocr: bool = True, include_apply: bool = False, include_rollback: bool = False, include_quality: bool = True) -> pd.DataFrame:
    status = build_stage_status(outputs_dir)
    plan_rows: list[dict] = []
    for spec in STAGES:
        if spec.branch == 'ocr' and not include_ocr:
            continue
        if spec.branch == 'ops' and not include_apply and spec.stage == '08_apply_changes':
            continue
        if spec.branch == 'ops' and not include_rollback and spec.stage in {'09_rollback', '10_post_apply_validation'}:
            continue
        if spec.branch == 'quality' and not include_quality:
            continue
        row = status.loc[status['stage'] == spec.stage].iloc[0]
        plan_rows.append({
            'stage': spec.stage,
            'notebook': spec.notebook,
            'branch': spec.branch,
            'optional': spec.optional,
            'status': 'done' if bool(row['available']) else 'pending',
            'latest_file': row['latest_file'],
            'description': spec.description,
        })
    return pd.DataFrame(plan_rows)


def infer_next_notebook(run_plan: pd.DataFrame) -> Optional[str]:
    pending = run_plan.loc[run_plan['status'] == 'pending']
    if pending.empty:
        return None
    return str(pending.iloc[0]['notebook'])


def summarize_outputs(outputs_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    for path in sorted(outputs_dir.glob('*')):
        if not path.is_file():
            continue
        rows.append({
            'name': path.name,
            'suffix': path.suffix.lower(),
            'size_bytes': path.stat().st_size,
            'modified_at': pd.Timestamp(path.stat().st_mtime, unit='s'),
        })
    if not rows:
        return pd.DataFrame(columns=['name', 'suffix', 'size_bytes', 'modified_at'])
    return pd.DataFrame(rows).sort_values('modified_at', ascending=False).reset_index(drop=True)


def export_orchestration_tables(status_df: pd.DataFrame, run_plan_df: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / 'pipeline_status_latest.csv'
    plan_path = output_dir / 'pipeline_plan_latest.csv'
    status_df.to_csv(status_path, index=False)
    run_plan_df.to_csv(plan_path, index=False)
    return status_path, plan_path
