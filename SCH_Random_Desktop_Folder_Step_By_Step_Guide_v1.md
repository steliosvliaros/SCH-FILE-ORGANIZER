# SCH File Organizer — Step-by-Step Guide for a Random Desktop Folder

## Purpose
Use this guide when you have a **single messy folder on your Desktop** with random files and you want to process it safely with the SCH File Organizer pipeline.

This guide assumes:
- your local repo is already set up
- your conda environment works
- the notebooks already run cleanly
- your current source-of-truth policy is **`SCH_fileserver_policy_v2_4.yaml`**

---

# 1. Golden rule before you start

**Do not run the first live test on the original Desktop folder.**

Always work in this order:
1. keep the original folder untouched
2. make a copied working folder
3. run the pipeline on the copy
4. review the outputs
5. only then allow a small live apply on the copy
6. when satisfied, repeat the same method on larger folders

---

# 2. Example scenario

Assume your Desktop contains this folder:

`C:\Users\User\Desktop\Random_Files`

This is the original folder.

Create a working copy, for example:

`C:\Users\User\Desktop\Random_Files_WORKING_COPY`

Use the **working copy** in the pipeline, not the original.

---

# 3. Recommended folder preparation

## 3.1 Backup
Before anything else:
- keep the original folder untouched
- if the files matter, make one extra backup copy somewhere safe

## 3.2 Make a working copy
In Windows Explorer:
- right click the Desktop folder
- copy
- paste
- rename the pasted copy to something clear like:
  - `Random_Files_WORKING_COPY`

## 3.3 Keep the batch small if needed
If the folder is large or chaotic, first test on a subset such as:
- 50–200 files
- mixed file types
- some PDFs, Word files, Excel files, images, junk files, duplicates if available

---

# 4. Open the project

1. Open **VS Code**
2. Open your **SCH file organizer repo folder**
3. Select the correct conda interpreter, for example:
   - `Python (schfs)`
4. Open the notebooks pane

---

# 5. Confirm the policy file

Make sure the repo contains:

`policy/SCH_fileserver_policy_v2_4.yaml`

This is the current policy source of truth.

---

# 6. Choose the scan root

For this use case, your scan root should be the working copy, for example:

```python
SCAN_ROOT = Path(r"C:\Users\User\Desktop\Random_Files_WORKING_COPY")
```

Do **not** point the first real run to:
- the original Desktop folder
- a live company file server
- a large unreviewed root with thousands of files

---

# 7. The exact notebook order for a random Desktop folder

Run the notebooks in this order.

## Step 1 — Policy check
Open:

`01_policy_check.ipynb`

Run all cells.

Expected result:
- policy loads correctly
- no schema errors
- type codes, folder buckets, document types, and filename template display correctly

If this fails:
- stop
- fix the policy loading issue first

---

## Step 2 — Inventory the working copy
Open:

`02_inventory.ipynb`

Set:

```python
SCAN_ROOT = Path(r"C:\Users\User\Desktop\Random_Files_WORKING_COPY")
```

Run all cells.

Expected result:
- inventory parquet/csv created
- file counts visible
- extension mix visible
- duplicates by hash visible
- long-path risks visible

What this does:
- scans the folder
- records metadata
- computes hashes
- does **not** change files

---

## Step 3 — Rule classification
Open:

`03_rule_classification.ipynb`

Run all cells.

Expected result:
- rows classified into statuses such as:
  - junk
  - duplicate
  - compliant-like
  - review

What this does:
- applies deterministic first-pass rules
- identifies junk/system/noise files
- identifies exact duplicates
- identifies items that still need review

---

## Step 4 — Extract text
Open:

`04_extract_text.ipynb`

Run all cells.

Expected result:
- text extracted where possible
- row-level errors only for bad files
- no whole-notebook crash

What this does:
- extracts text from TXT, MD, CSV, JSON, YAML, DOCX, text PDFs, Excel preview, and similar readable files
- still does **not** rename or move anything

---

## Step 5 — Review outputs
Open:

`05_review_outputs.ipynb`

Run all cells.

Review carefully:
- junk candidates
- duplicates
- extraction failures
- long paths
- compliant items
- manual-review queue

This is the first serious quality checkpoint.

If the folder is truly random, expect many rows to remain in review. That is normal.

---

## Step 6 — Dry-run planner
Open:

`06_planner.ipynb`

Run all cells.

For a random Desktop folder, many rows may still remain in:
- `manual_review`
- `archive_or_delete_review`
- `review_special_folder_policy`

That is acceptable. This notebook should still give you a structured dry-run plan.

---

## Step 7 — Execution manifest
Open:

`07_execution_manifest.ipynb`

Run all cells.

Expected result:
- executable manifest
- keep register
- blocked rows
- review queue
- rollback manifest

For a random first batch, it is common to have:
- few executable rows
- many review rows

That is a healthy result.

---

# 8. If the folder contains many scans or image PDFs

If many files are:
- scanned PDFs
- images
- screenshots
- phone photos of documents

then continue with OCR.

## Step 8 — OCR candidate check
Open:

`15_ocr_layer.ipynb`

First run with:

```python
ENABLE_OCR = False
```

Review the OCR candidate list.

Then, on a small batch only, set:

```python
ENABLE_OCR = True
```

Run again.

Expected result:
- OCR text extracted for scanned/image-only files
- row-level errors only

---

# 9. Canonical rename/path generation

Once text and OCR are available where needed, run the deterministic rename/path layer.

## Step 9 — Canonical engine
Open:

`11_canonical_rename_engine.ipynb`

Run all cells.

Expected result:
- some rows become `canonical_ready`
- some rows remain unresolved
- long-path handling is applied deterministically

This notebook does **not** guess freely.
It uses the YAML rules first.

---

# 10. LLM suggestions for unresolved files

If many files are still unresolved, especially random ones with vague names like:
- `scan1.pdf`
- `document.pdf`
- `IMG_3021.jpg`
- `new file.docx`

then use the suggestion layer.

## Step 10 — LLM suggestions
Open:

`12_llm_suggestions.ipynb`

Run all cells.

This layer should:
- read extracted file content
- infer likely description from inside the file
- suggest doc type, phase, date, version, status, and related fields when possible
- stay conservative when evidence is weak

Important:
- this notebook suggests fields only
- it does **not** decide final paths by itself

---

# 11. Feed accepted suggestions back into canonicalization

## Step 11 — Feedback loop
Open:

`13_feedback_loop.ipynb`

Run all cells.

Expected result:
- accepted suggestions are fed back into canonical inputs
- deterministic canonicalization reruns
- some previously unresolved rows become canonical-ready

If OCR was used and the files were scan-heavy, also run:

`16_ocr_feedback_loop.ipynb`

and if needed:

`17_ocr_feedback_to_execution.ipynb`

---

# 12. Promote newly ready rows back into planning

## Step 12 — Feedback to execution
Open:

`14_feedback_to_execution.ipynb`

Run all cells.

Expected result:
- newly canonical-ready rows are promoted into planner-style actions
- unresolved rows remain in review

This closes the loop from:
- weak file
- extracted content / OCR
- suggestion
- canonical rerun
- ready-for-planning row

---

# 13. Before any live file changes

Before you allow any real move/rename, confirm all of the following:

- the working copy is still the target, not the original folder
- review outputs look reasonable
- canonical names look reasonable
- no suspicious bulk moves are queued
- no placeholder paths remain
- the batch is small enough to inspect manually

Recommended first live batch size:
- 10 to 30 files

---

# 14. Dry-run apply first

## Step 13 — Apply notebook in dry-run mode
Open:

`08_apply_changes.ipynb`

Set:

```python
DRY_RUN = True
```

Run all cells.

Expected result:
- apply log created
- no real file changes made

Review the log carefully.

If the dry-run looks wrong:
- stop
- go back to review / canonicalization / suggestions

---

# 15. Small live apply on the working copy only

Only when the dry-run looks correct.

In:

`08_apply_changes.ipynb`

set:

```python
DRY_RUN = False
```

Keep the batch very small.

Recommended first live apply:
- 10 files
- or one small subfolder only

Expected result:
- files move/rename on the **working copy only**
- apply log created
- rollback possible

---

# 16. Validate what happened

## Step 14 — Post-apply validation
Open:

`10_post_apply_validation.ipynb`

Run all cells.

Expected result:
- `validated_move` for real successful moves
- `dry_run_not_applied` if you only simulated
- clear identification of rows needing manual review

This proves whether the filesystem state matches the plan.

---

# 17. Roll back if needed

If the live apply did something wrong:

## Step 15 — Rollback
Open:

`09_rollback.ipynb`

Run first in dry-run mode, then live only if needed.

Use rollback only on the working copy.

---

# 18. Use the control notebooks if you want guided operation

Instead of remembering every notebook, you can use:

## Master control
`18_master_orchestration.ipynb`

## Preset selector
`19_batch_presets.ipynb`

Useful preset examples for a Desktop folder:
- `review_only`
- `canonicalize_unresolved`
- `ocr_rescue`
- `promote_to_execution`
- `apply_small_live_batch`

## Guided batch runner
`20_batch_runner.ipynb`

This helps you run a preset sequence with stop points.

---

# 19. What to expect with truly random Desktop files

A random Desktop folder often contains:
- temporary downloads
- personal notes
- screenshots
- mixed contracts / invoices / scans
- duplicate copies
- bad filenames
- old junk files
- system files

So expect this behavior:
- many files will classify quickly by rules
- many files will still need review
- scans may need OCR
- description quality improves significantly after text extraction and OCR
- the pipeline becomes stronger after the feedback loop reruns

This is normal.

---

# 20. What happens to long paths

Long paths should not be handled randomly.

The deterministic policy order is:
1. shorten description
2. shorten project name
3. shorten location
4. use abbreviation table
5. add short hash suffix
6. last resort: `_LONGPATH` fallback according to policy

So if a random Desktop file becomes canonical-ready but the final path is too long, it should be shortened by deterministic policy logic, not by arbitrary guessing.

---

# 21. Recommended first-use workflow for your Desktop folder

For your first real test, use exactly this order:

1. copy `Desktop\\Random_Files` to `Desktop\\Random_Files_WORKING_COPY`
2. run `01_policy_check.ipynb`
3. run `02_inventory.ipynb` on the working copy
4. run `03_rule_classification.ipynb`
5. run `04_extract_text.ipynb`
6. run `05_review_outputs.ipynb`
7. if scan-heavy, run `15_ocr_layer.ipynb`
8. run `11_canonical_rename_engine.ipynb`
9. run `12_llm_suggestions.ipynb`
10. run `13_feedback_loop.ipynb`
11. run `14_feedback_to_execution.ipynb`
12. run `07_execution_manifest.ipynb`
13. run `08_apply_changes.ipynb` with `DRY_RUN = True`
14. inspect logs
15. run `08_apply_changes.ipynb` with `DRY_RUN = False` on a tiny batch only
16. run `10_post_apply_validation.ipynb`
17. if needed, run `09_rollback.ipynb`

---

# 22. When you should stop and review manually

Stop and review if:
- too many files remain unresolved
- descriptions look generic or wrong
- many target paths look strange
- a folder appears to receive unrelated files
- lots of rows are blocked
- OCR output is noisy or low quality
- live dry-run suggests more moves than expected

When in doubt:
- stop
- lower the batch size
- rerun review and feedback

---

# 23. Best practice summary

For a random Desktop folder:
- work on a copy first
- inventory first, never rename first
- use rules first
- use OCR only where needed
- use the LLM only to suggest missing fields from file content
- rerun deterministic canonicalization after suggestions
- dry-run before live apply
- apply only small batches
- validate after apply
- keep rollback ready

---

# 24. Final recommendation

Your safest and most effective operating pattern is:

**copy → scan → extract → review → canonicalize → suggest → feedback rerun → dry-run apply → live small batch → validate → expand**

That is the correct way to use the system on a random Desktop folder.
