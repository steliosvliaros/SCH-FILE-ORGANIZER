from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import pandas as pd

from .orchestrator import (
    build_run_plan,
    build_stage_status,
    infer_next_notebook,
)


@dataclass(frozen=True)
class BatchPreset:
    name: str
    description: str
    include_ocr: bool = False
    include_apply: bool = False
    include_rollback: bool = False
    include_quality: bool = False
    goal: str = ''
    notes: str = ''


PRESETS: tuple[BatchPreset, ...] = (
    BatchPreset(
        name='review_only',
        description='Run the safe read-only core pipeline up to review outputs.',
        include_ocr=False,
        include_apply=False,
        include_rollback=False,
        include_quality=False,
        goal='Inspect current files, duplicates, junk, extraction quality, and review queue without planning or execution.',
        notes='Best default for a new batch or after policy changes.',
    ),
    BatchPreset(
        name='canonicalize_unresolved',
        description='Run the quality branch to canonicalize unresolved rows and enrich with suggestions.',
        include_ocr=False,
        include_apply=False,
        include_rollback=False,
        include_quality=True,
        goal='Improve deterministic canonical paths and names for unresolved rows without touching files.',
        notes='Use after review outputs and before any execution work.',
    ),
    BatchPreset(
        name='ocr_rescue',
        description='Run the OCR branch for scanned PDFs and image-only files, then feed them back into canonicalization.',
        include_ocr=True,
        include_apply=False,
        include_rollback=False,
        include_quality=True,
        goal='Rescue weak/no-text files so content-derived fields can be suggested and canonicalized.',
        notes='Use only on a small sandbox or a targeted subset first.',
    ),
    BatchPreset(
        name='promote_to_execution',
        description='Promote newly canonical-ready rows back into planner outputs and rebuild manifests.',
        include_ocr=True,
        include_apply=False,
        include_rollback=False,
        include_quality=True,
        goal='Convert accepted deterministic results into planner-style rows and execution manifests, still without moving files.',
        notes='Use after feedback reruns have improved unresolved rows.',
    ),
    BatchPreset(
        name='apply_small_live_batch',
        description='Prepare and execute a small approved batch with full logs and rollback support.',
        include_ocr=True,
        include_apply=True,
        include_rollback=False,
        include_quality=True,
        goal='Run a tiny live batch only after dry-run, review, and manifest checks are clean.',
        notes='Always use a copied sandbox or a tightly bounded approved batch.',
    ),
    BatchPreset(
        name='rollback_last_batch',
        description='Rollback the most recent moved batch and then validate the filesystem state.',
        include_ocr=True,
        include_apply=True,
        include_rollback=True,
        include_quality=True,
        goal='Reverse the last applied batch and verify observed-on-disk state.',
        notes='Only meaningful after a real moved batch exists.',
    ),
)


def list_presets() -> pd.DataFrame:
    rows = [asdict(p) for p in PRESETS]
    return pd.DataFrame(rows)


def get_preset(name: str) -> BatchPreset:
    normalized = str(name).strip().lower()
    for preset in PRESETS:
        if preset.name == normalized:
            return preset
    valid = ', '.join(p.name for p in PRESETS)
    raise KeyError(f'Unknown preset {name!r}. Valid presets: {valid}')


def build_preset_plan(outputs_dir: Path, preset_name: str) -> pd.DataFrame:
    preset = get_preset(preset_name)
    plan = build_run_plan(
        outputs_dir,
        include_ocr=preset.include_ocr,
        include_apply=preset.include_apply,
        include_rollback=preset.include_rollback,
        include_quality=preset.include_quality,
    ).copy()
    plan.insert(0, 'preset_name', preset.name)
    plan.insert(1, 'preset_goal', preset.goal)
    plan['preset_notes'] = preset.notes
    plan['recommended'] = plan['status'].eq('pending')
    return plan


def build_preset_status(outputs_dir: Path, preset_name: str) -> pd.DataFrame:
    preset = get_preset(preset_name)
    status = build_stage_status(outputs_dir).copy()
    allowed_branches = {'core'}
    if preset.include_quality:
        allowed_branches.add('quality')
    if preset.include_ocr:
        allowed_branches.add('ocr')
    if preset.include_apply or preset.include_rollback:
        allowed_branches.add('ops')
    status = status[status['branch'].isin(allowed_branches)].reset_index(drop=True)
    status.insert(0, 'preset_name', preset.name)
    return status


def summarize_preset(outputs_dir: Path, preset_name: str) -> pd.DataFrame:
    preset = get_preset(preset_name)
    plan = build_preset_plan(outputs_dir, preset_name)
    next_notebook = infer_next_notebook(plan[['stage', 'notebook', 'branch', 'optional', 'status', 'latest_file', 'description']])
    done_count = int((plan['status'] == 'done').sum())
    pending_count = int((plan['status'] == 'pending').sum())
    return pd.DataFrame([{
        'preset_name': preset.name,
        'description': preset.description,
        'goal': preset.goal,
        'notes': preset.notes,
        'done_count': done_count,
        'pending_count': pending_count,
        'next_notebook': next_notebook,
    }])


def export_preset_tables(summary_df: pd.DataFrame, status_df: pd.DataFrame, plan_df: pd.DataFrame, output_dir: Path, stem: str = 'pipeline_preset') -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f'{stem}_summary_latest.csv'
    status_path = output_dir / f'{stem}_status_latest.csv'
    plan_path = output_dir / f'{stem}_plan_latest.csv'
    summary_df.to_csv(summary_path, index=False)
    status_df.to_csv(status_path, index=False)
    plan_df.to_csv(plan_path, index=False)
    return summary_path, status_path, plan_path
