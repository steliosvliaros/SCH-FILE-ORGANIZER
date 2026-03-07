from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
import yaml

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None


DATE_PATTERNS = [
    re.compile(r'(?P<date>20\d{2}[01]\d[0-3]\d)'),
    re.compile(r'(?P<date>20\d{2}[-_/][01]\d[-_/][0-3]\d)'),
    re.compile(r'(?P<date>[0-3]\d[-_/][01]\d[-_/]20\d{2})'),
]
VERSION_PATTERN = re.compile(r'\b(v\d{1,2}|rev[-_ ]?\d{1,2}|revision[-_ ]?\d{1,2}|r\d{1,2})\b', re.IGNORECASE)
STATUS_PATTERN = re.compile(r'\b(DRAFT|FINAL|SIGNED|ISSUED|RECEIVED|SUPERSEDED|ASBUILT|APPROVED|FORREVIEW|FOR-REVIEW|IFC|AFC)\b', re.IGNORECASE)
CANONICAL_TYPEID_PATTERN = re.compile(r'\b([A-Z]{2,3}\d{2}p\d{3}-\d{2})\b')
PHASE_TOKEN_PATTERN = re.compile(r'\b([A-Z]{2})\b')
TOKEN_SPLIT_PATTERN = re.compile(r'[^A-Za-z0-9]+')


def load_policy(policy_path: Path | str) -> dict:
    path = Path(policy_path)
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def _safe_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value).strip()


@dataclass
class SuggestionConfig:
    use_ollama: bool = False
    ollama_base_url: str = 'http://localhost:11434'
    ollama_model: str = 'llama3.1:8b-instruct-q4_K_M'
    timeout_seconds: int = 120
    max_rows: int = 50
    require_unresolved_only: bool = True
    include_text_preview_chars: int = 8000
    temperature: float = 0.0
    prefer_content_for_description: bool = True


SUGGESTION_COLUMNS = [
    'relative_path',
    'filename',
    'unresolved_fields',
    'suggested_phase',
    'suggested_doc_type',
    'suggested_date',
    'suggested_version',
    'suggested_status',
    'suggested_description',
    'suggested_description_source',
    'suggested_company_folder',
    'suggested_asset_folder',
    'suggested_owner_or_project',
    'suggested_location',
    'suggested_namecode',
    'suggested_typ3',
    'suggested_vat9',
    'suggestion_confidence',
    'suggestion_source',
    'suggestion_reason',
    'suggestion_evidence',
    'suggestion_prompt',
    'suggestion_raw_response',
]


REVIEW_TEXT_COLUMNS = [
    'extracted_text', 'text_preview', 'text_source', 'text_status', 'text_error',
    'canonical_notes', 'canonical_reason', 'canonical_phase', 'canonical_doc_type',
    'canonical_date', 'canonical_version', 'canonical_status', 'canonical_description',
]


def normalize_suggestion_input(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if 'relative_path' not in out.columns:
        out['relative_path'] = ''
    if 'filename' not in out.columns:
        if 'name' in out.columns:
            out['filename'] = out['name'].fillna('').astype(str).map(lambda x: Path(x).name)
        else:
            out['filename'] = out['relative_path'].fillna('').astype(str).map(lambda x: Path(x).name)
    if 'suffix' not in out.columns:
        out['suffix'] = out['filename'].fillna('').astype(str).map(lambda x: Path(x).suffix.lower().lstrip('.'))
    if 'text_preview' not in out.columns:
        if 'extracted_text' in out.columns:
            out['text_preview'] = out['extracted_text'].fillna('').astype(str).str[:3000]
        else:
            out['text_preview'] = ''
    if 'extracted_text' not in out.columns:
        out['extracted_text'] = out.get('text_preview', '')
    if 'unresolved_fields' not in out.columns:
        out['unresolved_fields'] = ''
    if 'canonical_ready' not in out.columns:
        out['canonical_ready'] = False
    for col in REVIEW_TEXT_COLUMNS:
        if col not in out.columns:
            out[col] = ''
    return out


def filter_rows_for_suggestions(df: pd.DataFrame, config: Optional[SuggestionConfig] = None) -> pd.DataFrame:
    config = config or SuggestionConfig()
    out = normalize_suggestion_input(df)
    if config.require_unresolved_only:
        mask = (~out['canonical_ready'].fillna(False)) | (out['unresolved_fields'].fillna('').astype(str) != '')
        out = out.loc[mask].copy()
    if config.max_rows and len(out) > config.max_rows:
        out = out.head(config.max_rows).copy()
    return out


def _policy_phase_codes(policy: Mapping[str, Any]) -> List[str]:
    vals = policy.get('controlled_vocabularies', {}).get('phase_codes', {})
    return list(vals.keys())


def _policy_doc_types(policy: Mapping[str, Any]) -> List[str]:
    return list((policy.get('document_types', {}) or {}).keys())


def _policy_status_tags(policy: Mapping[str, Any]) -> List[str]:
    tags = policy.get('controlled_vocabularies', {}).get('status_tags', []) or []
    return [str(t).upper() for t in tags]


def _extract_date(text: str) -> str:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group('date')
            digits = re.sub(r'\D', '', raw)
            if len(digits) == 8 and digits.startswith('20'):
                return digits
            if len(digits) == 8 and digits.endswith(tuple(str(y) for y in range(2000, 2100))):
                # ddmmyyyy -> yyyymmdd
                return digits[4:] + digits[2:4] + digits[:2]
    return ''


def _extract_version(text: str) -> str:
    match = VERSION_PATTERN.search(text)
    if not match:
        return ''
    raw = match.group(1).lower().replace(' ', '').replace('_', '').replace('-', '')
    num = re.sub(r'\D', '', raw)
    if not num:
        return ''
    return f'v{int(num):02d}'


def _extract_status(text: str, policy: Mapping[str, Any]) -> str:
    tags = set(_policy_status_tags(policy))
    match = STATUS_PATTERN.search(text.upper())
    if not match:
        return ''
    token = match.group(1).upper().replace('-', '')
    if token == 'FORREVIEW' and 'FOR_REVIEW' in tags:
        return 'FOR_REVIEW'
    if token in tags:
        return token
    for tag in tags:
        if tag.replace('_', '') == token:
            return tag
    return ''


def _extract_typeid(text: str) -> str:
    match = CANONICAL_TYPEID_PATTERN.search(text)
    if not match:
        return ''
    typeid = match.group(1).upper()
    if typeid.startswith('PV') and not typeid.startswith('PVS'):
        typeid = 'PVS' + typeid[2:]
    return typeid


def _suggest_phase(text: str, policy: Mapping[str, Any]) -> str:
    phase_codes = set(_policy_phase_codes(policy))
    parts = [p.upper() for p in TOKEN_SPLIT_PATTERN.split(text) if p]
    for part in parts:
        if part in phase_codes:
            return part
    return ''


def _suggest_doc_type(text: str, policy: Mapping[str, Any]) -> str:
    doc_types = set(_policy_doc_types(policy))
    parts = [p.upper() for p in TOKEN_SPLIT_PATTERN.split(text) if p]
    for part in parts:
        if part in doc_types:
            return part
    # simple semantic heuristics
    lowered = text.lower()
    mapping = [
        ('permit', ['permit', 'license', 'authorisation', 'authorization']),
        ('drawing', ['drawing', 'layout', 'plan', 'dwg']),
        ('report', ['report', 'study', 'assessment']),
        ('contract', ['contract', 'agreement']),
        ('invoice', ['invoice', 'bill']),
    ]
    for label, keys in mapping:
        if any(k in lowered for k in keys):
            for doc in doc_types:
                if label in doc.lower():
                    return doc
    return ''


TITLE_STOPWORDS = {
    'page', 'confidential', 'draft', 'final', 'version', 'revision', 'rev', 'date', 'issued',
    'approved', 'received', 'company', 'document', 'file', 'scan', 'untitled', 'newdocument'
}


def _normalize_description_candidate(value: str) -> str:
    value = _safe_str(value)
    value = value.replace('_', '-')
    value = re.sub(r'\s+', '-', value)
    value = re.sub(r'[^A-Za-z0-9-]+', '-', value)
    value = re.sub(r'-{2,}', '-', value).strip('-')
    return value[:80]


def _is_title_like_line(line: str) -> bool:
    line = _safe_str(line)
    if not line:
        return False
    if len(line) < 8 or len(line) > 120:
        return False
    lowered = line.lower()
    if lowered in TITLE_STOPWORDS:
        return False
    if re.fullmatch(r'[\d\W_]+', line):
        return False
    digits = sum(ch.isdigit() for ch in line)
    letters = sum(ch.isalpha() for ch in line)
    if letters == 0 or digits > letters:
        return False
    words = [w for w in re.split(r'\s+', line) if w]
    if len(words) > 14:
        return False
    return True


def _extract_title_candidates_from_text(text: str) -> List[Tuple[str, str]]:
    text = _safe_str(text)
    lines = [re.sub(r'\s+', ' ', ln).strip(' -_:.\t') for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    out: List[Tuple[str, str]] = []

    keyword_patterns = [
        re.compile(r'^(?:subject|title|project|description)\s*[:\-]\s*(.+)$', re.IGNORECASE),
    ]

    for ln in lines[:40]:
        for pat in keyword_patterns:
            m = pat.match(ln)
            if m:
                cand = _normalize_description_candidate(m.group(1))
                if cand:
                    out.append((cand, f'content_field:{ln[:80]}'))

    for idx, ln in enumerate(lines[:25]):
        if _is_title_like_line(ln):
            cand = _normalize_description_candidate(ln)
            if cand and cand.lower() not in TITLE_STOPWORDS:
                out.append((cand, f'content_line_{idx+1}:{ln[:80]}'))

    return out


def _suggest_description(text: str, filename: str, unresolved_fields: Sequence[str], prefer_content: bool = True) -> Tuple[str, str]:
    candidates: List[Tuple[str, str]] = []

    if prefer_content:
        candidates.extend(_extract_title_candidates_from_text(text))

    stem = Path(filename).stem
    stem = re.sub(r'^[A-Z]{2,3}\d{2}p\d{3}-\d{2}_[A-Z]{2}_[A-Z]{3,6}_', '', stem)
    stem = re.sub(r'_20\d{6}_v\d{2}_[A-Z_]+$', '', stem)
    stem = _normalize_description_candidate(stem)
    if stem and stem.lower() not in {'document1', 'newdocument', 'scan', 'untitled'}:
        candidates.append((stem, 'filename_stem'))

    preview = _safe_str(text)[:400]
    preview = re.sub(r'\s+', ' ', preview)
    preview = re.sub(r'[^A-Za-z0-9 -]+', ' ', preview)
    words = [w for w in preview.split(' ') if w]
    if words:
        phrase = _normalize_description_candidate(' '.join(words[:8]))
        if len(phrase) >= 8:
            candidates.append((phrase, 'content_preview'))

    seen = set()
    for cand, source in candidates:
        if cand and cand not in seen:
            seen.add(cand)
            return cand, source
    return '', ''


def _suggest_company_fields(text: str, path_text: str) -> Dict[str, str]:
    # conservative placeholder extractor; relies mostly on path hints
    merged = f'{path_text} {text}'
    vat_match = re.search(r'\b(\d{9})\b', merged)
    company_match = re.search(r'\b([A-Z][A-Z0-9]{2,})-(AEE|EPE|EEE|OEE|IKE|MIK)-(\d{9})\b', merged, re.IGNORECASE)
    out = {
        'suggested_namecode': '',
        'suggested_typ3': '',
        'suggested_vat9': '',
        'suggested_company_folder': '',
    }
    if company_match:
        out['suggested_namecode'] = company_match.group(1).upper()
        out['suggested_typ3'] = company_match.group(2).upper()
        out['suggested_vat9'] = company_match.group(3)
    elif vat_match:
        out['suggested_vat9'] = vat_match.group(1)
    if out['suggested_namecode'] and out['suggested_typ3'] and out['suggested_vat9']:
        out['suggested_company_folder'] = f"{out['suggested_namecode']}-{out['suggested_typ3']}-{out['suggested_vat9']}"
    return out


def _heuristic_suggestion(row: Mapping[str, Any], policy: Mapping[str, Any], config: Optional[SuggestionConfig] = None) -> Dict[str, Any]:
    config = config or SuggestionConfig()
    filename = _safe_str(row.get('filename'))
    rel = _safe_str(row.get('relative_path'))
    unresolved = [u for u in _safe_str(row.get('unresolved_fields')).split(';') if u]
    text = _safe_str(row.get('extracted_text') or row.get('text_preview'))
    merged = ' '.join([filename, rel, text[:4000]])
    suggested_description, suggested_description_source = _suggest_description(
        text=text,
        filename=filename,
        unresolved_fields=unresolved,
        prefer_content=config.prefer_content_for_description,
    )

    suggested = {
        'relative_path': rel,
        'filename': filename,
        'unresolved_fields': ';'.join(unresolved),
        'suggested_phase': _safe_str(row.get('canonical_phase')) or _suggest_phase(merged, policy),
        'suggested_doc_type': _safe_str(row.get('canonical_doc_type')) or _suggest_doc_type(merged, policy),
        'suggested_date': _safe_str(row.get('canonical_date')) or _extract_date(merged),
        'suggested_version': _safe_str(row.get('canonical_version')) or _extract_version(merged) or 'v01',
        'suggested_status': _safe_str(row.get('canonical_status')) or _extract_status(merged, policy) or 'DRAFT',
        'suggested_description': _safe_str(row.get('canonical_description')) or suggested_description,
        'suggested_description_source': suggested_description_source,
        'suggested_company_folder': '',
        'suggested_asset_folder': '',
        'suggested_owner_or_project': '',
        'suggested_location': '',
        'suggested_namecode': '',
        'suggested_typ3': '',
        'suggested_vat9': '',
        'suggestion_confidence': 0.0,
        'suggestion_source': 'heuristic',
        'suggestion_reason': '',
        'suggestion_evidence': '',
        'suggestion_prompt': '',
        'suggestion_raw_response': '',
    }
    suggested.update(_suggest_company_fields(text, rel))

    hits = 0
    evidence: List[str] = []
    for key in ['suggested_phase', 'suggested_doc_type', 'suggested_date', 'suggested_version', 'suggested_status', 'suggested_description']:
        if suggested[key]:
            hits += 1
            evidence.append(f"{key}={suggested[key]}")
    if suggested['suggested_company_folder']:
        hits += 1
        evidence.append(f"company={suggested['suggested_company_folder']}")

    confidence = min(0.92, 0.15 + hits * 0.11)
    if text:
        confidence += 0.05
    suggested['suggestion_confidence'] = round(min(confidence, 0.95), 2)
    if suggested.get('suggested_description_source', '').startswith('content'):
        suggested['suggestion_reason'] = 'heuristic extraction from file content with filename/path fallback'
    else:
        suggested['suggestion_reason'] = 'heuristic extraction from filename/path/text preview'
    suggested['suggestion_evidence'] = '; '.join(evidence)
    return suggested


PROMPT_TEMPLATE = """You are helping organize files according to a strict YAML policy.
Return ONLY valid JSON with these keys:
- suggested_phase
- suggested_doc_type
- suggested_date
- suggested_version
- suggested_status
- suggested_description
- suggested_description_source
- suggested_company_folder
- suggested_asset_folder
- suggested_owner_or_project
- suggested_location
- suggested_namecode
- suggested_typ3
- suggested_vat9
- suggestion_confidence
- suggestion_reason
- suggestion_evidence

Rules:
- Use the policy vocabularies exactly when possible.
- Never invent certainty. If unknown, return empty string.
- Date must be YYYYMMDD.
- Version must be like v01.
- Confidence must be between 0 and 1.
- Suggest fields only; do not output final file paths.
- Deduce suggested_description from the file's internal content first: title, subject, heading, project line, or first meaningful heading. Use filename/path only if content is not useful.
- suggested_description should be concise, specific, and kebab-case style text suitable for the DESCRIPTION token.

Allowed phase codes: {phase_codes}
Allowed status tags: {status_tags}
Known document types: {doc_types}
Unresolved fields for this row: {unresolved_fields}

Filename: {filename}
Relative path: {relative_path}
Text preview:
{text_preview}
"""


def build_prompt_for_row(row: Mapping[str, Any], policy: Mapping[str, Any], config: Optional[SuggestionConfig] = None) -> str:
    config = config or SuggestionConfig()
    preview = _safe_str(row.get('extracted_text') or row.get('text_preview'))[:config.include_text_preview_chars]
    return PROMPT_TEMPLATE.format(
        phase_codes=', '.join(_policy_phase_codes(policy)),
        status_tags=', '.join(_policy_status_tags(policy)),
        doc_types=', '.join(_policy_doc_types(policy)),
        unresolved_fields=_safe_str(row.get('unresolved_fields')),
        filename=_safe_str(row.get('filename')),
        relative_path=_safe_str(row.get('relative_path')),
        text_preview=preview,
    )


def _parse_ollama_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def _ollama_generate(prompt: str, config: SuggestionConfig) -> Dict[str, Any]:
    if requests is None:
        raise RuntimeError('requests is not installed')
    url = config.ollama_base_url.rstrip('/') + '/api/generate'
    payload = {
        'model': config.ollama_model,
        'prompt': prompt,
        'stream': False,
        'options': {'temperature': config.temperature},
        'format': 'json',
    }
    resp = requests.post(url, json=payload, timeout=config.timeout_seconds)
    resp.raise_for_status()
    data = resp.json()
    raw = _safe_str(data.get('response'))
    parsed = _parse_ollama_json(raw)
    return {'raw': raw, 'parsed': parsed}


def _merge_suggestion_payload(base: Dict[str, Any], payload: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key in SUGGESTION_COLUMNS:
        if key in payload and key not in {'relative_path', 'filename', 'unresolved_fields'}:
            out[key] = payload[key]
    return out


def suggest_row(row: Mapping[str, Any], policy: Mapping[str, Any], config: Optional[SuggestionConfig] = None) -> Dict[str, Any]:
    config = config or SuggestionConfig()
    base = _heuristic_suggestion(row, policy, config=config)
    prompt = build_prompt_for_row(row, policy, config=config)
    base['suggestion_prompt'] = prompt

    if not config.use_ollama:
        return base

    try:
        response = _ollama_generate(prompt, config)
        parsed = response.get('parsed') or {}
        merged = _merge_suggestion_payload(base, parsed)
        merged['suggestion_source'] = 'ollama'
        if not merged.get('suggested_description_source') and merged.get('suggested_description'):
            merged['suggested_description_source'] = 'ollama_content_inference'
        merged['suggestion_raw_response'] = response.get('raw', '')
        if 'suggestion_confidence' in parsed:
            try:
                merged['suggestion_confidence'] = round(float(parsed['suggestion_confidence']), 2)
            except Exception:
                pass
        return merged
    except Exception as exc:
        base['suggestion_source'] = 'heuristic_fallback'
        base['suggestion_reason'] = f"{base['suggestion_reason']}; ollama_error:{exc}"
        return base


def suggest_dataframe(df: pd.DataFrame, policy: Mapping[str, Any], config: Optional[SuggestionConfig] = None) -> pd.DataFrame:
    config = config or SuggestionConfig()
    rows = filter_rows_for_suggestions(df, config=config).to_dict(orient='records')
    out = pd.DataFrame([suggest_row(row, policy, config=config) for row in rows])
    if out.empty:
        return pd.DataFrame(columns=SUGGESTION_COLUMNS)
    for col in SUGGESTION_COLUMNS:
        if col not in out.columns:
            out[col] = ''
    return out[SUGGESTION_COLUMNS]


def merge_suggestions_into_candidates(candidates_df: pd.DataFrame, suggestions_df: pd.DataFrame) -> pd.DataFrame:
    left = normalize_suggestion_input(candidates_df)
    right = suggestions_df.copy()
    if right.empty:
        out = left.copy()
        for col in SUGGESTION_COLUMNS:
            if col not in out.columns:
                out[col] = ''
        return out
    merged = left.merge(right, on=['relative_path', 'filename', 'unresolved_fields'], how='left', suffixes=('', '_suggestion'))
    for col in SUGGESTION_COLUMNS:
        if col not in merged.columns:
            merged[col] = ''
    return merged
