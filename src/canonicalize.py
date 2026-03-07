from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd
import yaml

INVALID_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*]+')
MULTI_DASH_PATTERN = re.compile(r'-{2,}')
NON_ASCII_PUNCT_PATTERN = re.compile(r'[^A-Za-z0-9._-]+')
STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'into', 'onto', 'draft', 'final', 'copy', 'new',
    'document', 'file', 'version', 'rev', 'revised', 'signed', 'received'
}


@dataclass
class CanonicalizeConfig:
    company_folder_override: Optional[str] = None
    asset_folder_override: Optional[str] = None
    archive_year: Optional[str] = None
    assume_active_assets: bool = True
    abbreviations: Optional[Mapping[str, str]] = None
    max_description_chars_override: Optional[int] = None
    max_project_name_chars_override: Optional[int] = None
    max_location_chars_override: Optional[int] = None


LEGACY_CANONICAL_PATTERN = re.compile(
    r'^(?P<typeid>[A-Z]{2,3}\d{2}p\d{3}-\d{2})_'
    r'(?P<phase>[A-Z]{2})_'
    r'(?P<doctype>[A-Z]{3,6})_'
    r'(?P<description>.+)_'
    r'(?P<date>\d{8})_'
    r'(?P<version>v\d{2})_'
    r'(?P<status>[A-Z]+)$'
)


def load_policy(policy_path: Path | str) -> dict:
    path = Path(policy_path)
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def _safe_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value).strip()


def _coalesce(mapping: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        if key in mapping:
            value = _safe_str(mapping[key])
            if value:
                return value
    return ''


def normalize_token(value: Any, *, allow_dot: bool = False) -> str:
    text = _safe_str(value)
    if not text:
        return ''
    text = text.replace(' ', '-')
    text = INVALID_CHARS_PATTERN.sub('-', text)
    if not allow_dot:
        text = text.replace('.', '-')
    text = NON_ASCII_PUNCT_PATTERN.sub('-', text)
    text = MULTI_DASH_PATTERN.sub('-', text)
    text = text.strip('-. _')
    return text


def normalize_extension(value: Any) -> str:
    ext = _safe_str(value).lower().lstrip('.')
    return ext


def _first_hash6(values: Iterable[str]) -> str:
    joined = '|'.join([v for v in values if v])
    return hashlib.blake2b(joined.encode('utf-8'), digest_size=6).hexdigest()[:6]


def _get_limits(policy: Mapping[str, Any], config: CanonicalizeConfig) -> Dict[str, int]:
    limits = (
        policy.get('path_length_limits', {}).get('recommended_limits', {})
        or policy.get('naming_policy', {}).get('constraints', {})
        or {}
    )
    return {
        'max_full_path_chars': int(limits.get('max_full_path_chars', 240)),
        'max_filename_chars': int(limits.get('max_filename_chars', 120)),
        'max_foldername_chars': int(limits.get('max_foldername_chars', 64)),
        'max_description_chars': int(config.max_description_chars_override or limits.get('max_description_chars', 60)),
        'max_project_name_chars': int(config.max_project_name_chars_override or limits.get('max_project_name_chars', 40)),
        'max_location_chars': int(config.max_location_chars_override or limits.get('max_location_chars', 30)),
        'max_depth_segments': int(limits.get('max_depth_segments', 12)),
    }


def parse_canonical_filename(filename: str) -> Dict[str, str]:
    path = Path(filename)
    stem = path.stem
    m = LEGACY_CANONICAL_PATTERN.match(stem)
    if not m:
        return {}
    parsed = m.groupdict()
    parsed['ext'] = path.suffix.lower().lstrip('.')
    if parsed['typeid'].startswith('PV') and not parsed['typeid'].startswith('PVS'):
        parsed['typeid'] = 'PVS' + parsed['typeid'][2:]
    return parsed


def encode_typeid(type_code: str, metric_value: Any, sequence: Any, *, unit: Optional[str] = None, policy: Optional[Mapping[str, Any]] = None) -> str:
    type_code = normalize_token(type_code).upper()
    if not type_code:
        raise ValueError('Missing type code')

    type_defs = (((policy or {}).get('naming_policy', {}).get('typeid', {}) or {}).get('encoding', {}) or {}).get('types', {})
    type_meta = type_defs.get(type_code, {})

    seq_text = f"{int(float(sequence)):02d}"

    metric_number = None
    metric_text = _safe_str(metric_value)
    if metric_text:
        try:
            metric_number = float(metric_text)
        except ValueError:
            metric_number = None

    if metric_number is None:
        metric_token = normalize_token(metric_value)
        if re.fullmatch(r'\d{2}p\d{3}', metric_token):
            return f'{type_code}{metric_token}-{seq_text}'
        raise ValueError('Missing or invalid metric value')

    unit_text = _safe_str(unit).lower()
    if type_code in {'PVS', 'WND', 'DTC'}:
        if unit_text in {'mw', ''}:
            base_value = round(metric_number * 1000)
        else:
            base_value = round(metric_number)
    elif type_code == 'BES':
        if unit_text in {'mwh'}:
            base_value = round(metric_number * 1000)
        else:
            base_value = round(metric_number)
    elif type_code == 'HYP':
        if unit_text in {'ha'}:
            base_value = round(metric_number * 10000)
        else:
            base_value = round(metric_number)
    else:
        base_value = round(metric_number)

    metric_token = f'{base_value // 1000:02d}p{base_value % 1000:03d}'
    return f'{type_code}{metric_token}-{seq_text}'


def build_company_folder(fields: Mapping[str, Any]) -> str:
    return '_'.join([]) if False else '-'.join([
        normalize_token(_coalesce(fields, ['namecode', 'NAMECODE', 'company_code'])).upper(),
        normalize_token(_coalesce(fields, ['typ3', 'TYP3', 'legal_form'])).upper(),
        re.sub(r'\D', '', _coalesce(fields, ['vat9', 'VAT9', 'vat', 'company_vat'])).zfill(9),
    ]).strip('-')


def build_asset_folder(fields: Mapping[str, Any], policy: Mapping[str, Any]) -> str:
    typeid = _coalesce(fields, ['typeid', 'TYPEID'])
    if not typeid:
        typeid = encode_typeid(
            _coalesce(fields, ['type', 'TYPE']),
            _coalesce(fields, ['metric', 'METRIC', 'metric_value']),
            _coalesce(fields, ['ss', 'SS', 'sequence']),
            unit=_coalesce(fields, ['metric_unit', 'unit']),
            policy=policy,
        )
    project_name = normalize_token(_coalesce(fields, ['project_name', 'PROJECT_NAME', 'owner_or_project']))
    location = normalize_token(_coalesce(fields, ['location', 'LOCATION']))
    return '_'.join([typeid, project_name, location]).strip('_')


def resolve_phase_folder(policy: Mapping[str, Any], phase: str) -> str:
    return _safe_str(policy.get('folder_buckets', {}).get('PHASE_FOLDER_MAP', {}).get(phase.upper()))


def resolve_doc_folder(policy: Mapping[str, Any], doc_type: str, phase: str) -> str:
    doc = policy.get('document_types', {}).get(doc_type.upper(), {})
    folder = _safe_str(doc.get('default_folder'))
    phase_folder = resolve_phase_folder(policy, phase)
    if '{PHASE_FOLDER}' in folder:
        folder = folder.replace('{PHASE_FOLDER}', phase_folder)
    return folder


def build_internal_filename(fields: Mapping[str, Any], policy: Mapping[str, Any]) -> str:
    ext = normalize_extension(_coalesce(fields, ['ext', 'suffix', 'extension']))
    if not ext:
        raise ValueError('Missing extension')

    typeid = _coalesce(fields, ['typeid', 'TYPEID'])
    if not typeid:
        typeid = encode_typeid(
            _coalesce(fields, ['type', 'TYPE']),
            _coalesce(fields, ['metric', 'METRIC', 'metric_value']),
            _coalesce(fields, ['ss', 'SS', 'sequence']),
            unit=_coalesce(fields, ['metric_unit', 'unit']),
            policy=policy,
        )

    phase = normalize_token(_coalesce(fields, ['phase', 'PHASE'])).upper()
    doc_type = normalize_token(_coalesce(fields, ['doc_type', 'DOCTYPE', 'doctype'])).upper()
    description = normalize_token(_coalesce(fields, ['description', 'DESCRIPTION']))
    date = re.sub(r'\D', '', _coalesce(fields, ['date', 'DATE']))
    version = normalize_token(_coalesce(fields, ['version', 'VERSION'])).lower()
    status = normalize_token(_coalesce(fields, ['status', 'STATUS'])).upper()

    return f'{typeid}_{phase}_{doc_type}_{description}_{date}_{version}_{status}.{ext}'


def _truncate_token(value: str, max_len: int, *, abbreviations: Optional[Mapping[str, str]] = None) -> str:
    token = normalize_token(value)
    if len(token) <= max_len:
        return token

    abbreviations = abbreviations or {}
    parts = [p for p in token.split('-') if p]
    shortened: List[str] = []
    for part in parts:
        if part.lower() in STOPWORDS:
            continue
        lowered = part.lower()
        if lowered in abbreviations:
            shortened.append(abbreviations[lowered])
        else:
            shortened.append(part)
    token = '-'.join(shortened) or token
    if len(token) <= max_len:
        return token
    return token[:max_len].rstrip('-_ .')


def enforce_policy_lengths(
    *,
    company_folder: str,
    asset_folder: str,
    lifecycle_subpath: str,
    filename: str,
    row: Mapping[str, Any],
    policy: Mapping[str, Any],
    config: CanonicalizeConfig,
) -> Dict[str, Any]:
    limits = _get_limits(policy, config)
    actions: List[str] = []

    asset_parts = asset_folder.split('_')
    if len(asset_parts) >= 3:
        typeid, project_name, location = asset_parts[0], asset_parts[1], '_'.join(asset_parts[2:])
    else:
        typeid, project_name, location = asset_folder, '', ''

    stem, ext = Path(filename).stem, Path(filename).suffix
    stem_parts = stem.split('_')
    description = stem_parts[3] if len(stem_parts) >= 7 else ''

    project_name_new = _truncate_token(project_name, limits['max_project_name_chars'], abbreviations=config.abbreviations)
    if project_name_new != project_name:
        actions.append('shorten_project_name')
        project_name = project_name_new

    location_new = _truncate_token(location, limits['max_location_chars'], abbreviations=config.abbreviations)
    if location_new != location:
        actions.append('shorten_location')
        location = location_new

    description_new = _truncate_token(description, limits['max_description_chars'], abbreviations=config.abbreviations)
    if description_new != description:
        actions.append('shorten_description')
        description = description_new
        stem_parts[3] = description
        filename = '_'.join(stem_parts) + ext

    asset_folder = '_'.join([p for p in [typeid, project_name, location] if p])
    rel_path = '/'.join([p for p in [company_folder, 'ASSETS' if config.assume_active_assets else 'ARCHIVE', config.archive_year or '', asset_folder, lifecycle_subpath, filename] if p])

    if len(filename) > limits['max_filename_chars']:
        over = len(filename) - limits['max_filename_chars']
        description_new = _truncate_token(description, max(10, len(description) - over), abbreviations=config.abbreviations)
        if description_new != description:
            actions.append('shorten_description_for_filename')
            description = description_new
            stem_parts[3] = description
            filename = '_'.join(stem_parts) + ext

    rel_path = '/'.join([p for p in [company_folder, 'ASSETS' if config.assume_active_assets else 'ARCHIVE', config.archive_year or '', asset_folder, lifecycle_subpath, filename] if p])
    if len(rel_path) > limits['max_full_path_chars']:
        hash6 = _first_hash6([
            _safe_str(row.get('relative_path')),
            _safe_str(row.get('blake2b_hash')),
            filename,
        ])
        stem_obj = Path(filename).stem
        ext2 = Path(filename).suffix
        budget = max(20, limits['max_filename_chars'] - len(f'-h{hash6}{ext2}'))
        truncated_stem = _truncate_token(stem_obj, budget, abbreviations=config.abbreviations)
        filename = f'{truncated_stem}-h{hash6}{ext2}'
        actions.append('append_hash_suffix')
        rel_path = '/'.join([p for p in [company_folder, 'ASSETS' if config.assume_active_assets else 'ARCHIVE', config.archive_year or '', asset_folder, lifecycle_subpath, filename] if p])

    if len(rel_path) > limits['max_full_path_chars']:
        parts = lifecycle_subpath.split('/') if lifecycle_subpath else []
        if parts:
            lifecycle_subpath = '/'.join([parts[0], 'Working', '_LONGPATH'])
        else:
            lifecycle_subpath = 'Working/_LONGPATH'
        actions.append('move_to_longpath_folder')
        rel_path = '/'.join([p for p in [company_folder, 'ASSETS' if config.assume_active_assets else 'ARCHIVE', config.archive_year or '', asset_folder, lifecycle_subpath, filename] if p])

    return {
        'canonical_asset_folder': asset_folder,
        'canonical_filename': filename,
        'canonical_relative_path': rel_path,
        'canonical_lifecycle_subpath': lifecycle_subpath,
        'length_actions': ';'.join(actions),
        'full_path_len': len(rel_path),
        'filename_len': len(filename),
        'max_full_path_chars': limits['max_full_path_chars'],
        'max_filename_chars': limits['max_filename_chars'],
        'is_within_limits': len(rel_path) <= limits['max_full_path_chars'] and len(filename) <= limits['max_filename_chars'],
    }


def canonicalize_row(row: Mapping[str, Any], policy: Mapping[str, Any], config: Optional[CanonicalizeConfig] = None) -> Dict[str, Any]:
    config = config or CanonicalizeConfig()
    unresolved: List[str] = []
    notes: List[str] = []

    parsed = parse_canonical_filename(_coalesce(row, ['filename', 'name', 'basename']))

    working: Dict[str, Any] = {**parsed, **{k: v for k, v in row.items()}}

    if config.company_folder_override:
        company_folder = config.company_folder_override
        notes.append('used_company_folder_override')
    else:
        company_folder = _coalesce(working, ['company_folder'])
        if not company_folder:
            company_folder = build_company_folder(working)
        if not company_folder or company_folder.count('-') < 2:
            unresolved.append('company_folder')

    typeid = _coalesce(working, ['typeid', 'TYPEID'])
    if typeid.startswith('PV') and not typeid.startswith('PVS'):
        typeid = 'PVS' + typeid[2:]
        notes.append('mapped_legacy_pv_to_pvs')
        working['typeid'] = typeid

    if config.asset_folder_override:
        asset_folder = config.asset_folder_override
        notes.append('used_asset_folder_override')
    else:
        try:
            asset_folder = _coalesce(working, ['asset_folder']) or build_asset_folder(working, policy)
        except Exception:
            asset_folder = ''
        if not asset_folder:
            unresolved.append('asset_folder')

    phase = normalize_token(_coalesce(working, ['phase', 'PHASE'])).upper()
    if not phase:
        unresolved.append('phase')
    elif phase not in policy.get('controlled_vocabularies', {}).get('phase_codes', {}):
        unresolved.append('phase_invalid')

    doc_type = normalize_token(_coalesce(working, ['doc_type', 'DOCTYPE', 'doctype'])).upper()
    if not doc_type:
        unresolved.append('doc_type')
    elif doc_type not in policy.get('document_types', {}):
        unresolved.append('doc_type_invalid')

    date = re.sub(r'\D', '', _coalesce(working, ['date', 'DATE']))
    if len(date) != 8:
        unresolved.append('date')

    version = normalize_token(_coalesce(working, ['version', 'VERSION'])).lower()
    if not re.fullmatch(r'v\d{2}', version):
        unresolved.append('version')

    status = normalize_token(_coalesce(working, ['status', 'STATUS'])).upper()
    if status not in set(policy.get('controlled_vocabularies', {}).get('status_tags', [])):
        unresolved.append('status')

    description = normalize_token(_coalesce(working, ['description', 'DESCRIPTION']))
    if not description:
        unresolved.append('description')

    ext = normalize_extension(_coalesce(working, ['ext', 'suffix', 'extension']))
    if not ext:
        unresolved.append('ext')

    try:
        filename = build_internal_filename({**working, 'typeid': typeid}, policy)
    except Exception as exc:
        filename = ''
        notes.append(f'filename_build_error:{exc}')
        unresolved.append('typeid_or_filename_parts')

    lifecycle_subpath = ''
    if phase and doc_type and phase in policy.get('controlled_vocabularies', {}).get('phase_codes', {}) and doc_type in policy.get('document_types', {}):
        lifecycle_subpath = resolve_doc_folder(policy, doc_type, phase)
        if not lifecycle_subpath:
            unresolved.append('lifecycle_folder')
    else:
        unresolved.append('lifecycle_folder')

    canonical: Dict[str, Any] = {
        'relative_path': _coalesce(working, ['relative_path']),
        'filename': _coalesce(working, ['filename', 'name', 'basename']),
        'canonical_company_folder': company_folder,
        'canonical_asset_folder': asset_folder,
        'canonical_lifecycle_subpath': lifecycle_subpath,
        'canonical_filename': filename,
        'canonical_phase': phase,
        'canonical_doc_type': doc_type,
        'canonical_date': date,
        'canonical_version': version,
        'canonical_status': status,
        'canonical_description': description,
        'unresolved_fields': ';'.join(dict.fromkeys(unresolved)),
        'canonical_notes': ';'.join(notes),
        'canonical_ready': len(unresolved) == 0,
    }

    if canonical['canonical_ready']:
        length_info = enforce_policy_lengths(
            company_folder=company_folder,
            asset_folder=asset_folder,
            lifecycle_subpath=lifecycle_subpath,
            filename=filename,
            row=working,
            policy=policy,
            config=config,
        )
        canonical.update(length_info)
        canonical['canonical_reason'] = 'ready_deterministic'
    else:
        canonical.update({
            'canonical_relative_path': '',
            'length_actions': '',
            'full_path_len': None,
            'filename_len': None,
            'max_full_path_chars': _get_limits(policy, config)['max_full_path_chars'],
            'max_filename_chars': _get_limits(policy, config)['max_filename_chars'],
            'is_within_limits': False,
            'canonical_reason': 'missing_required_fields',
        })

    return canonical


def canonicalize_dataframe(df: pd.DataFrame, policy: Mapping[str, Any], config: Optional[CanonicalizeConfig] = None) -> pd.DataFrame:
    rows = [canonicalize_row(rec, policy, config=config) for rec in df.to_dict(orient='records')]
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=[
            'relative_path', 'filename', 'canonical_company_folder', 'canonical_asset_folder',
            'canonical_lifecycle_subpath', 'canonical_filename', 'canonical_relative_path',
            'canonical_phase', 'canonical_doc_type', 'canonical_date', 'canonical_version',
            'canonical_status', 'canonical_description', 'unresolved_fields', 'canonical_notes',
            'canonical_ready', 'length_actions', 'full_path_len', 'filename_len',
            'max_full_path_chars', 'max_filename_chars', 'is_within_limits', 'canonical_reason',
        ])
    return out
