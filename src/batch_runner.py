from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import pandas as pd

from .batch_presets import build_preset_plan, get_preset
from .orchestrator import find_project_root


@dataclass(frozen=True)
class RunnerConfig:
    preset_name: str
    stop_after_review: bool = True
    stop_after_manifest: bool = True
    stop_before_apply: bool = True
    stop_before_rollback: bool = True
    include_optional_done_steps: bool = False
    allow_live_apply: bool = False
    notes: str = ''


_STOP_NOTEBOOKS = {
    '05_review_outputs.ipynb': 'Review checkpoint before planning or any execution work.',
    '07_execution_manifest.ipynb': 'Manifest checkpoint before any apply action.',
    '08_apply_changes.ipynb': 'Live apply gate. Keep dry-run unless you explicitly approve a tiny batch.',
    '09_rollback.ipynb': 'Rollback gate. Use only after a real moved batch exists.',
}


def _gate_for_notebook(notebook: str, cfg: RunnerConfig) -> tuple[bool, str]:
    if notebook == '05_review_outputs.ipynb' and cfg.stop_after_review:
        return True, _STOP_NOTEBOOKS[notebook]
    if notebook == '07_execution_manifest.ipynb' and cfg.stop_after_manifest:
        return True, _STOP_NOTEBOOKS[notebook]
    if notebook == '08_apply_changes.ipynb':
        if cfg.stop_before_apply or not cfg.allow_live_apply:
            reason = _STOP_NOTEBOOKS[notebook]
            if not cfg.allow_live_apply:
                reason += ' Live apply is disabled in RunnerConfig.'
            return True, reason
    if notebook == '09_rollback.ipynb' and cfg.stop_before_rollback:
        return True, _STOP_NOTEBOOKS[notebook]
    return False, ''


def build_batch_sequence(outputs_dir: Path, cfg: RunnerConfig) -> pd.DataFrame:
    plan = build_preset_plan(outputs_dir, cfg.preset_name).copy()
    if not cfg.include_optional_done_steps:
        plan = plan.loc[~((plan['status'] == 'done') & (plan['optional'].fillna(False)))].copy()

    rows: list[dict] = []
    run_order = 1
    for _, row in plan.iterrows():
        should_stop, stop_reason = _gate_for_notebook(str(row['notebook']), cfg)
        action = 'run'
        if row['status'] == 'done':
            action = 'already_done'
        if should_stop:
            action = 'stop_after_run' if row['status'] != 'done' else 'stop_checkpoint'
        rows.append({
            'run_order': run_order,
            'preset_name': cfg.preset_name,
            'stage': row['stage'],
            'notebook': row['notebook'],
            'branch': row['branch'],
            'status': row['status'],
            'optional': bool(row['optional']),
            'description': row['description'],
            'runner_action': action,
            'stop_point': should_stop,
            'stop_reason': stop_reason,
            'preset_goal': row['preset_goal'],
            'preset_notes': row['preset_notes'],
        })
        run_order += 1
    return pd.DataFrame(rows)


def summarize_batch_sequence(sequence_df: pd.DataFrame, cfg: RunnerConfig) -> pd.DataFrame:
    pending = sequence_df.loc[sequence_df['status'] == 'pending']
    next_run = pending.iloc[0]['notebook'] if not pending.empty else None
    next_stop = sequence_df.loc[sequence_df['stop_point']].iloc[0]['notebook'] if sequence_df['stop_point'].any() else None
    return pd.DataFrame([{
        'preset_name': cfg.preset_name,
        'description': get_preset(cfg.preset_name).description,
        'goal': get_preset(cfg.preset_name).goal,
        'steps_total': int(len(sequence_df)),
        'steps_pending': int((sequence_df['status'] == 'pending').sum()),
        'steps_done': int((sequence_df['status'] == 'done').sum()),
        'stop_points': int(sequence_df['stop_point'].sum()),
        'next_run_notebook': next_run,
        'first_stop_notebook': next_stop,
        'allow_live_apply': cfg.allow_live_apply,
        'notes': cfg.notes,
    }])


def build_powershell_commands(project_root: Path, sequence_df: pd.DataFrame, kernel_name: str = 'schfs', output_dir_name: str = 'data/runner_logs') -> list[str]:
    project_root = find_project_root(project_root)
    lines = [
        f"Set-Location '{project_root}'",
        f"$env:PYTHONPATH = '{project_root};' + $env:PYTHONPATH",
        f"New-Item -ItemType Directory -Force -Path '{project_root / output_dir_name}' | Out-Null",
        ''
    ]
    for _, row in sequence_df.iterrows():
        nb = Path('notebooks') / str(row['notebook'])
        lines.append(f"Write-Host 'Stage {int(row['run_order'])}: {row['stage']} -> {row['notebook']}'")
        if row['status'] == 'done':
            lines.append("Write-Host 'Already done - review if you want to rerun.'")
        else:
            lines.append(
                "jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 "
                f"'{nb}'"
            )
        if bool(row['stop_point']):
            msg = str(row['stop_reason']).replace("'", "''")
            lines.append(f"Write-Host '{msg}'")
            lines.append("Read-Host 'Checkpoint reached. Press Enter to continue or Ctrl+C to stop'")
        lines.append('')
    return lines


def export_batch_runner_tables(summary_df: pd.DataFrame, sequence_df: pd.DataFrame, output_dir: Path, stem: str = 'batch_runner') -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f'{stem}_summary_latest.csv'
    sequence_path = output_dir / f'{stem}_sequence_latest.csv'
    powershell_path = output_dir / f'{stem}_run_latest.ps1'
    summary_df.to_csv(summary_path, index=False)
    sequence_df.to_csv(sequence_path, index=False)
    return summary_path, sequence_path, powershell_path
