# SCH File Organizer

Rules-first local file reorganization toolkit for applying the `SCH_fileserver_policy_v2_3.yaml` standard.

## Current scope

- Validate and inspect policy YAML
- Inventory folders recursively with hashes and path-risk indicators
- Apply deterministic first-pass rules to inventory outputs
- Produce dry-run classification tables for manual review in Jupyter / VS Code

## Recommended workflow

1. Run `notebooks/01_policy_check.ipynb`
2. Run `notebooks/02_inventory.ipynb` on a copied sandbox folder
3. Run `notebooks/03_rule_classification.ipynb`
4. Review CSV / Parquet outputs before any move or rename logic is added

## Layout

- `policy/` source-of-truth YAML
- `src/` reusable modules
- `notebooks/` runnable VS Code notebooks
- `data/outputs/` generated inventory and review files

## Safety

- Dry-run only
- No file moves or renames yet
- Test on a copied sandbox, not the live file server
