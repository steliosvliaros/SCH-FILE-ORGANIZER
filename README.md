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

## What each stage does

- `01_policy_check`: load and validate the YAML policy.
- `02_inventory`: scan a sandbox folder and write inventory CSV/Parquet.
- `03_rule_classification`: apply deterministic policy rules to inventory rows.
- `04_extract_text`: extract text previews from supported file types.
- `05_review_outputs`: merge outputs into one review frame.
- `06_planner`: create a conservative dry-run move/keep plan.
- `07_execution_manifest`: split the dry-run plan into executable manifest, keep register, review queue, blocked rows, and rollback manifest.

## Safety rules

- Work on a copied sandbox first, never the live master tree.
- Keep every stage dry-run until the review outputs look correct.
- Do not auto-enable special-folder conventions unless you intentionally adopt them.
- Keep rollback manifests for every executable batch.
