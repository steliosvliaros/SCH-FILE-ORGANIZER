# SCH File Organizer

Rules-first local file-server reorganization toolkit based on the SCH file-server YAML policy.

## Current scope
- Load and validate the YAML policy.
- Scan a root folder into a structured inventory.
- Apply deterministic first-pass rules to identify policy-compliant files, junk files, and review candidates.
- Review outputs in Jupyter Notebook inside VS Code.

## Safety principles
- Dry-run first.
- Never run on the live master tree before validating on a sandbox copy.
- Prefer deterministic rules over AI guesses.
- Keep rollback manifests for any later execution phase.
