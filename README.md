# SCH File Organizer

Rules-first local file-server reorganization toolkit based on the SCH file-server YAML policy.

## Current scope
- Load and validate the YAML policy.
- Scan a root folder into a structured inventory.
- Apply deterministic first-pass rules to identify policy-compliant files, junk files, duplicates, and review candidates.
- Review outputs in Jupyter Notebook inside VS Code.

## Safety principles
- Dry-run first.
- Never run on the live master tree before validating on a sandbox copy.
- Prefer deterministic rules over AI guesses.
- Keep rollback manifests for any later execution phase.

## Notebook flow
- `01_policy_check.ipynb` — validate the YAML and render sample folders / filenames.
- `02_inventory.ipynb` — scan a sandbox root, write CSV / Parquet outputs, and review counts, duplicates, and path risks.

## Current modules
- `src/policy_loader.py` — policy loading, validation, naming helpers, regex generation.
- `src/inventory.py` — recursive inventory scanner with hashing and CSV / Parquet export.
- `src/rules.py` — deterministic first-pass classifier.
