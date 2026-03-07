# SCH file organizer starter

Rules-first local file-server reorganization scaffold for VS Code + Miniconda + Jupyter.

## Working policy

Use `policy/SCH_fileserver_policy_v2_4.yaml` as the current source of truth unless you explicitly supersede it.

## Suggested run order

1. `notebooks/01_policy_check.ipynb`
2. `notebooks/02_inventory.ipynb`
3. `notebooks/03_rule_classification.ipynb`
4. `notebooks/04_extract_text.ipynb`
5. `notebooks/05_review_outputs.ipynb`
6. `notebooks/06_planner.ipynb`
7. `notebooks/07_execution_manifest.ipynb`
8. `notebooks/08_apply_changes.ipynb`
9. `notebooks/09_rollback.ipynb`
10. `notebooks/10_post_apply_validation.ipynb`
11. `notebooks/11_canonical_rename_engine.ipynb`
12. `notebooks/12_llm_suggestions.ipynb`
13. `notebooks/13_feedback_loop.ipynb`
14. `notebooks/14_feedback_to_execution.ipynb`

## What each stage does

- `01_policy_check`: load and validate the YAML policy.
- `02_inventory`: scan a sandbox folder and write inventory CSV/Parquet.
- `03_rule_classification`: apply deterministic policy rules to inventory rows.
- `04_extract_text`: extract text previews from supported file types.
- `05_review_outputs`: merge outputs into one review frame.
- `06_planner`: create a conservative dry-run move/keep plan.
- `07_execution_manifest`: split the dry-run plan into executable manifest, keep register, review queue, blocked rows, and rollback manifest.
- `08_apply_changes`: apply a small approved batch with dry-run default and logging.
- `09_rollback`: reverse rows that were actually moved.
- `10_post_apply_validation`: compare plan, apply log, and live filesystem state after a batch.
- `11_canonical_rename_engine`: deterministically build canonical company/asset/path/name targets from the YAML.
- `12_llm_suggestions`: suggest missing fields for unresolved rows, with content-first description inference.
- `13_feedback_loop`: accept selected suggestions, rerun deterministic canonicalization, and measure uplift.
- `14_feedback_to_execution`: promote newly canonical-ready rows back into planner-style outputs and optionally rebuild execution manifests.

## Safety rules

- Work on a copied sandbox first, never the live master tree.
- Keep every stage dry-run until the review outputs look correct.
- Keep the YAML as the source of truth for names and paths.
- Let the suggestion layer fill missing fields only; do not let it choose final paths directly.
- Keep rollback manifests and apply logs for every executable batch.
