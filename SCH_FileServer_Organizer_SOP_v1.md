# SCH File Server Organizer SOP Manual

## Purpose

This SOP explains how to operate the local file-server reorganization system built for **SOLAR CELLS HELLAS** using:

- VS Code
- Python
- Miniconda
- Jupyter notebooks
- the YAML policy as the source of truth
- deterministic rules first
- LLM suggestions only for unresolved cases

This system is designed to reorganize files safely, transparently, and reversibly.

---

## Core Principles

1. **YAML policy is the source of truth**
   - Folder structure, naming standards, type codes, phase codes, archive rules, and path-length rules come from the current policy file.

2. **Rules first, LLM second**
   - Deterministic rules always run before any LLM suggestions.
   - The LLM does not directly rename or move files.
   - The LLM only suggests missing fields for unresolved cases.

3. **Dry-run before live changes**
   - Every major stage must be tested on a sandbox or copied batch first.
   - No direct work on the live master file server until outputs are reviewed.

4. **Execution must be reversible**
   - All live moves must have logs and rollback support.

5. **Review before apply**
   - Human review remains mandatory for uncertain or policy-incomplete cases.

---

## Current Policy Assumption

This SOP assumes the active policy file is:

- `SCH_fileserver_policy_v2_4.yaml`

The policy currently defines, at a high level:

- company folder pattern: `{NAMECODE}-{TYP3}-{VAT9}`
- inside company folder:
  - `CORPORATE`
  - `TEMPLATES`
  - `ARCHIVE`
  - `ASSETS`
- asset folder pattern:
  - `{TYPEID}_{PROJECT_NAME}_{LOCATION}`
- internal filename pattern:
  - `{TYPEID}_{PHASE}_{DOCTYPE}_{DESCRIPTION}_{DATE}_{VERSION}_{STATUS}.{EXT}`
- PV type code:
  - `PVS`

If the policy changes, update the YAML first and rerun validation before using the pipeline.

---

## System Architecture

The system has five logical layers.

### 1. Policy Layer
Contains naming, folder, archive, and path rules.

### 2. Inventory and Extraction Layer
Scans files and reads available text and metadata.

### 3. Decision Layer
Uses deterministic logic to classify files and build canonical names and paths.

### 4. Suggestion Layer
Uses heuristics and optionally a local LLM to suggest missing metadata for unresolved files.

### 5. Execution and Control Layer
Builds manifests, applies approved moves, validates results, and supports rollback.

---

## Folder Expectations in the Project Repo

Recommended repo structure:

```text
sch-file-organizer/
  policy/
  notebooks/
  src/
  data/
    outputs/
    logs/
    working/
    runner_logs/
  tests/
  README.md
  environment.yml
```

---

## Environment Requirements

### Base tools
- Windows PC
- Miniconda
- VS Code
- Python extension
- Jupyter extension
- Git

### Python environment
Use the dedicated conda environment for the project.

Example:

```bash
conda create -n schfs python=3.11 -y
conda activate schfs
```

### Typical packages
- pandas
- pyarrow
- pyyaml
- openpyxl
- pypdf
- python-docx
- matplotlib
- rapidfuzz
- rich
- tqdm

### Optional OCR packages
- pytesseract
- pillow
- pypdfium2
- local Tesseract OCR binary installed on the machine

---

## Operating Modes

The system can be used in several modes.

### Review-only mode
Used when you want to understand the current state without proposing or applying moves.

### Canonicalization mode
Used to build deterministic target names and paths.

### OCR rescue mode
Used to recover text from scanned PDFs and image-only files.

### Promotion-to-execution mode
Used when canonical-ready rows should be converted back into plan and manifest rows.

### Apply mode
Used for carefully approved live batches only.

### Rollback mode
Used to reverse a previously applied batch.

---

## Notebook SOP by Stage

Below is the standard order of use.

### 01 - Policy validation
**Notebook:** `01_policy_check.ipynb`

**Purpose**
- Validate that the policy YAML loads correctly.
- Confirm expected top-level keys, vocabularies, and templates.

**Use when**
- the policy changes
- setup is new
- behavior looks inconsistent with the YAML

**Expected output**
- confirmation that the policy loaded successfully

---

### 02 - Inventory scan
**Notebook:** `02_inventory.ipynb`

**Purpose**
- Scan the selected folder tree.
- Collect file metadata, hashes, path lengths, suffixes, and other base fields.

**Use when**
- starting a new batch
- rescanning after major changes
- rebuilding a clean baseline

**Expected outputs**
- `inventory_*.csv`
- `inventory_*.parquet`

**Rule**
- Start on a copied sandbox or small batch, not the live master tree.

---

### 03 - Rule classification
**Notebook:** `03_rule_classification.ipynb`

**Purpose**
- Apply deterministic first-pass classification.
- Detect junk, duplicates, superseded files, compliant-looking files, and review items.

**Expected outputs**
- `rule_classification_*.csv`
- `rule_classification_*.parquet`

---

### 04 - Text extraction
**Notebook:** `04_extract_text.ipynb`

**Purpose**
- Extract text from supported files.
- Support content-based decisions.

**Typical supported types**
- txt
- md
- csv
- json
- yaml
- xml
- docx
- text PDFs
- xlsx previews

**Expected outputs**
- `inventory_with_text_*.csv`
- `inventory_with_text_*.parquet`

---

### 05 - Review outputs
**Notebook:** `05_review_outputs.ipynb`

**Purpose**
- Review duplicates, extraction failures, junk candidates, long-path risks, compliant files, and manual review rows.

**Expected outputs**
- `review_snapshot_latest.csv`
- `review_snapshot_latest.parquet`

---

### 06 - Dry-run planner
**Notebook:** `06_planner.ipynb`

**Purpose**
- Build initial dry-run actions.
- Suggest keep, move, review, or archive-related actions.

**Expected outputs**
- `plan_dry_run_*.csv`
- `plan_dry_run_*.parquet`

---

### 07 - Execution manifest
**Notebook:** `07_execution_manifest.ipynb`

**Purpose**
- Split plan rows into:
  - executable manifest
  - keep register
  - review queue
  - blocked rows
  - rollback manifest

**Expected behavior**
- empty executable manifest is allowed
- many rows may remain in review early on

---

### 08 - Apply changes
**Notebook:** `08_apply_changes.ipynb`

**Purpose**
- Apply approved manifest rows in small batches.

**Rules**
- default must remain dry-run first
- live apply only on a small reviewed batch
- no silent destructive changes
- unresolved placeholders must stay blocked

**Expected outputs**
- apply logs in csv/parquet/jsonl

---

### 09 - Rollback
**Notebook:** `09_rollback.ipynb`

**Purpose**
- Reverse rows that were actually moved.

**Rules**
- use dry-run first
- rollback only rows proven by the apply log

---

### 10 - Post-apply validation
**Notebook:** `10_post_apply_validation.ipynb`

**Purpose**
- Compare what was planned, what the apply log reported, and what currently exists on disk.

**Validation states may include**
- `validated_move`
- `dry_run_not_applied`
- `not_applied_or_needs_review`
- `move_not_observed`
- `missing_both_after_move`

---

### 11 - Canonical rename/path engine
**Notebook:** `11_canonical_rename_engine.ipynb`

**Purpose**
- Build canonical names and target paths using the YAML only.
- Explicitly identify unresolved fields.

**Important**
- this is deterministic
- no LLM required
- long-path handling belongs here

---

### 12 - LLM suggestions
**Notebook:** `12_llm_suggestions.ipynb`

**Purpose**
- Suggest only missing fields for unresolved rows.

**Important**
- suggestions do not directly rename or move files
- content inside the file is preferred for deriving `DESCRIPTION`
- filename/path are fallback sources only

**Typical suggestion fields**
- `suggested_phase`
- `suggested_doc_type`
- `suggested_date`
- `suggested_version`
- `suggested_status`
- `suggested_description`

---

### 13 - Feedback loop
**Notebook:** `13_feedback_loop.ipynb`

**Purpose**
- Apply accepted suggestion fields back into canonicalization.
- Rerun deterministic canonicalization.

**Goal**
- convert unresolved rows into canonical-ready rows

---

### 14 - Feedback to execution
**Notebook:** `14_feedback_to_execution.ipynb`

**Purpose**
- Promote newly canonical-ready rows back into planner-style actions and optionally back into execution manifests.

---

### 15 - OCR layer
**Notebook:** `15_ocr_layer.ipynb`

**Purpose**
- Run OCR on scanned PDFs and image-only files.

**Rule**
- run only when needed
- start with OCR candidates review before enabling OCR

---

### 16 - OCR feedback loop
**Notebook:** `16_ocr_feedback_loop.ipynb`

**Purpose**
- Merge OCR text into unresolved files.
- rerun suggestions and feedback using OCR-enriched content.

---

### 17 - OCR feedback to execution
**Notebook:** `17_ocr_feedback_to_execution.ipynb`

**Purpose**
- Promote OCR-rescued canonical-ready rows into planning and manifest generation.

---

### 18 - Master orchestration
**Notebook:** `18_master_orchestration.ipynb`

**Purpose**
- Inspect current pipeline state.
- Recommend the next notebook to run.

---

### 19 - Batch presets
**Notebook:** `19_batch_presets.ipynb`

**Purpose**
- Choose the correct preset for the intended run.

**Typical presets**
- `review_only`
- `canonicalize_unresolved`
- `ocr_rescue`
- `promote_to_execution`
- `apply_small_live_batch`
- `rollback_last_batch`

---

### 20 - Batch runner
**Notebook:** `20_batch_runner.ipynb`

**Purpose**
- Build a guided execution sequence for a preset.
- Export sequence files and a PowerShell guidance script.

**Important**
- this is still a controlled runner
- live apply remains opt-in

---

## How Renaming Works

### Correct sequence
1. deterministic rules from YAML
2. canonicalization
3. long-path enforcement
4. unresolved rows only go to suggestion layer
5. accepted suggestions go back into canonicalization
6. only canonical-ready rows can move forward safely

### What the LLM is allowed to do
The LLM may suggest:
- description
- doc type
- phase
- date
- version
- status
- project hints

The LLM may not:
- directly rename files without canonicalization
- directly move files
- bypass review or policy rules

---

## How Long Paths Are Handled

Long paths are handled by the deterministic canonical engine.

The intended shortening order is:
1. shorten `DESCRIPTION`
2. shorten `PROJECT_NAME`
3. shorten `LOCATION`
4. apply abbreviations
5. append a short uniqueness suffix if needed
6. last resort: move to a `_LONGPATH` fallback location if policy allows

Long paths must never be solved by arbitrary manual hacks outside the policy.

---

## Daily Safe Operating Procedure

For a normal review batch:

1. Run `18_master_orchestration.ipynb`
2. Run `19_batch_presets.ipynb` with `review_only` or `canonicalize_unresolved`
3. Run `02_inventory.ipynb`
4. Run `03_rule_classification.ipynb`
5. Run `04_extract_text.ipynb`
6. Run `05_review_outputs.ipynb`
7. If needed, run `11_canonical_rename_engine.ipynb`
8. If unresolved rows remain, run `12_llm_suggestions.ipynb`
9. Run `13_feedback_loop.ipynb`
10. Run `14_feedback_to_execution.ipynb`
11. Review outputs before any apply step

For a scanned-PDF-heavy batch:

1. Run inventory and extraction first
2. Run `15_ocr_layer.ipynb`
3. Run `16_ocr_feedback_loop.ipynb`
4. Run `17_ocr_feedback_to_execution.ipynb`
5. Review promoted rows before apply

For a small live batch:

1. Complete review and canonicalization first
2. Build execution manifest
3. Run `08_apply_changes.ipynb` in dry-run mode
4. Review logs
5. Run a very small live batch
6. Run `10_post_apply_validation.ipynb`
7. If needed, run `09_rollback.ipynb`

---

## Approval Rules Before Live Apply

Do not run live apply unless all are true:

- batch is a copied or clearly scoped approved set
- latest review snapshot is checked
- execution manifest is understood
- no unresolved placeholders remain
- no unexpected target collisions remain
- rollback manifest exists
- dry-run log is reviewed first

---

## Troubleshooting Guide

### Problem: missing columns like `suffix` or `apply_status`
**Cause**
- old parquet output loaded from older schema
- notebook using cached kernel state

**Action**
- restart kernel
- rerun from top
- regenerate latest upstream parquet if needed

### Problem: `ModuleNotFoundError: No module named 'src'`
**Cause**
- notebook launched with wrong working directory or missing project root in `sys.path`

**Action**
- restart kernel
- ensure notebook uses project-root path bootstrap cell

### Problem: empty executable manifest
**Meaning**
- not necessarily a failure
- often means rows still require review or more metadata

**Action**
- continue with canonicalization and suggestion loop

### Problem: OCR gives poor results
**Cause**
- low-quality scans
- missing Tesseract install
- OCR run on files that already had better extracted text

**Action**
- check OCR candidate selection
- verify Tesseract installation
- limit OCR to high-value weak-text files only

### Problem: many files remain unresolved
**Cause**
- missing metadata in filenames and content
- weak text extraction
- legacy naming not yet mapped strongly enough

**Action**
- use OCR if needed
- inspect unresolved field list
- improve abbreviation tables or deterministic mappings
- only then rely on LLM suggestions

---

## Quality Control Checklist

Before accepting a batch as complete, confirm:

- policy file is correct and current
- inventory exists for the batch
- review snapshot has been checked
- canonical-ready rows are sensible
- suggested descriptions come from file content when possible
- long-path rows are handled through policy logic
- apply logs exist for live batches
- post-apply validation has been run
- rollback path is available if needed

---

## What Counts as Success

A batch is successful when:

- files are moved only according to the YAML policy
- names are canonical or explicitly marked unresolved
- high-confidence rows move through the pipeline cleanly
- ambiguous rows remain visible for review instead of being guessed
- live changes are logged and reversible
- post-apply validation confirms the result on disk

---

## Recommended Next Governance Additions

Optional future enhancements:

- final audit/report notebook
- approved abbreviations registry
- policy-change changelog notebook
- batch sign-off template
- exception register for permanently non-standard files

---

## Final Rule

When in doubt:

- do not guess
- do not move live files
- do not bypass the YAML
- rerun the deterministic path first
- keep uncertain rows in review

