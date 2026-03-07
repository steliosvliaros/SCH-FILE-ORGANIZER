# SCH File Organizer

Rules-first local file reorganization toolkit for applying the `SCH_fileserver_policy_v2_4.yaml` standard.

## Current scope

- Validate and inspect policy YAML
- Inventory folders recursively with hashes and path-risk indicators
- Apply deterministic first-pass rules to inventory outputs
- Produce dry-run classification tables for manual review in Jupyter / VS Code
- Extract text from common document types for downstream review and classification
- Build a conservative dry-run planning table for move/keep/manual-review decisions

## Recommended workflow

1. Run `notebooks/01_policy_check.ipynb`
2. Run `notebooks/02_inventory.ipynb` on a copied sandbox folder
3. Run `notebooks/03_rule_classification.ipynb`
4. Run `notebooks/04_extract_text.ipynb` to enrich inventory rows with extracted text
5. Run `notebooks/05_review_outputs.ipynb` to inspect duplicates, junk, path risks, and review queues
6. Run `notebooks/06_planner.ipynb` to generate a dry-run move/keep plan

## Layout

- `policy/` source-of-truth YAML
- `src/` reusable modules
- `notebooks/` runnable VS Code notebooks
- `data/outputs/` generated inventory, review, and planning files

## Safety

- Dry-run only
- No file moves or renames yet
- Duplicate and superseded routing stay manual by default under `v2_4`
- Test on a copied sandbox, not the live file server
