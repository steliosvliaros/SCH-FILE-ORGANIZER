from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None

try:
    import pypdfium2 as pdfium  # type: ignore
except Exception:  # pragma: no cover
    pdfium = None


IMAGE_SUFFIXES = {'png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'webp'}
PDF_SUFFIXES = {'pdf'}
OCR_SUFFIXES = IMAGE_SUFFIXES | PDF_SUFFIXES


@dataclass
class OCRConfig:
    enabled: bool = False
    max_rows: int = 25
    max_pdf_pages: int = 3
    image_dpi: int = 220
    min_text_chars_to_skip: int = 80
    languages: str = 'eng'
    ocr_prefix: str = '[OCR] '
    max_output_chars: int = 12000
    prefer_existing_text: bool = True
    require_missing_or_weak_text: bool = True


def _safe_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value).strip()


OCR_RESULT_COLUMNS = [
    'relative_path', 'filename', 'suffix', 'ocr_candidate', 'ocr_status', 'ocr_error',
    'ocr_text', 'ocr_text_preview', 'ocr_source', 'ocr_pages_attempted', 'ocr_backend'
]


def ensure_ocr_input_schema(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if 'relative_path' not in out.columns:
        out['relative_path'] = ''
    if 'filename' not in out.columns:
        out['filename'] = out['relative_path'].fillna('').astype(str).map(lambda x: Path(x).name)
    if 'suffix' not in out.columns:
        out['suffix'] = out['filename'].fillna('').astype(str).map(lambda x: Path(x).suffix.lower().lstrip('.'))
    if 'text_preview' not in out.columns:
        if 'extracted_text' in out.columns:
            out['text_preview'] = out['extracted_text'].fillna('').astype(str).str[:3000]
        else:
            out['text_preview'] = ''
    if 'extracted_text' not in out.columns:
        out['extracted_text'] = out['text_preview']
    if 'text_status' not in out.columns:
        out['text_status'] = ''
    if 'text_error' not in out.columns:
        out['text_error'] = ''
    if 'full_path' not in out.columns:
        out['full_path'] = ''
    return out


def _weak_text_mask(df: pd.DataFrame, config: OCRConfig) -> pd.Series:
    text_len = df['extracted_text'].fillna('').astype(str).str.len()
    preview_len = df['text_preview'].fillna('').astype(str).str.len()
    weak = (text_len < config.min_text_chars_to_skip) & (preview_len < config.min_text_chars_to_skip)
    status = df['text_status'].fillna('').astype(str).str.lower()
    failed = status.isin({'error', 'failed', 'unsupported', 'empty'})
    return weak | failed


def identify_ocr_candidates(df: pd.DataFrame, config: Optional[OCRConfig] = None) -> pd.DataFrame:
    config = config or OCRConfig()
    out = ensure_ocr_input_schema(df)
    suffix_mask = out['suffix'].fillna('').astype(str).str.lower().isin(OCR_SUFFIXES)
    if config.require_missing_or_weak_text:
        candidate_mask = suffix_mask & _weak_text_mask(out, config)
    else:
        candidate_mask = suffix_mask
    out = out.assign(ocr_candidate=candidate_mask)
    candidates = out.loc[candidate_mask].copy()
    if config.max_rows and len(candidates) > config.max_rows:
        candidates = candidates.head(config.max_rows).copy()
    return candidates


def _tesseract_available() -> bool:
    return pytesseract is not None and Image is not None


def _ocr_image(image: Any, config: OCRConfig) -> str:
    if not _tesseract_available():
        raise RuntimeError('pytesseract_or_pillow_not_available')
    text = pytesseract.image_to_string(image, lang=config.languages)  # type: ignore[arg-type]
    return _safe_str(text)


def _ocr_pdf_with_fitz(path: Path, config: OCRConfig) -> Dict[str, Any]:
    if fitz is None:
        raise RuntimeError('fitz_not_available')
    doc = fitz.open(path)
    texts: List[str] = []
    pages = min(len(doc), max(1, config.max_pdf_pages))
    for page_index in range(pages):
        page = doc.load_page(page_index)
        pix = page.get_pixmap(dpi=config.image_dpi)
        if Image is None:
            raise RuntimeError('pillow_not_available')
        mode = 'RGBA' if pix.alpha else 'RGB'
        image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
        texts.append(_ocr_image(image, config))
    combined = '\n\n'.join([t for t in texts if t])[: config.max_output_chars]
    return {'text': combined, 'pages_attempted': pages, 'backend': 'fitz+pytesseract'}


def _ocr_pdf_with_pdfium(path: Path, config: OCRConfig) -> Dict[str, Any]:
    if pdfium is None:
        raise RuntimeError('pdfium_not_available')
    pdf = pdfium.PdfDocument(str(path))
    texts: List[str] = []
    pages = min(len(pdf), max(1, config.max_pdf_pages))
    scale = max(config.image_dpi / 72.0, 1.0)
    for page_index in range(pages):
        page = pdf[page_index]
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        texts.append(_ocr_image(pil_image, config))
    combined = '\n\n'.join([t for t in texts if t])[: config.max_output_chars]
    return {'text': combined, 'pages_attempted': pages, 'backend': 'pdfium+pytesseract'}


def ocr_file(path: Path | str, config: Optional[OCRConfig] = None) -> Dict[str, Any]:
    config = config or OCRConfig()
    file_path = Path(path)
    suffix = file_path.suffix.lower().lstrip('.')
    result = {
        'ocr_status': 'not_run',
        'ocr_error': '',
        'ocr_text': '',
        'ocr_text_preview': '',
        'ocr_source': '',
        'ocr_pages_attempted': 0,
        'ocr_backend': '',
    }

    if not config.enabled:
        result['ocr_status'] = 'disabled'
        return result

    if not file_path.exists():
        result['ocr_status'] = 'missing_file'
        result['ocr_error'] = 'file_not_found'
        return result

    try:
        if suffix in IMAGE_SUFFIXES:
            if Image is None:
                raise RuntimeError('pillow_not_available')
            image = Image.open(file_path)
            text = _ocr_image(image, config)
            result.update({
                'ocr_status': 'success' if text else 'empty',
                'ocr_text': text[: config.max_output_chars],
                'ocr_text_preview': text[:1000],
                'ocr_source': 'ocr_image',
                'ocr_pages_attempted': 1,
                'ocr_backend': 'pytesseract',
            })
            return result

        if suffix in PDF_SUFFIXES:
            last_error = None
            for runner in (_ocr_pdf_with_fitz, _ocr_pdf_with_pdfium):
                try:
                    pdf_result = runner(file_path, config)
                    text = _safe_str(pdf_result.get('text', ''))
                    result.update({
                        'ocr_status': 'success' if text else 'empty',
                        'ocr_text': text[: config.max_output_chars],
                        'ocr_text_preview': text[:1000],
                        'ocr_source': 'ocr_pdf',
                        'ocr_pages_attempted': int(pdf_result.get('pages_attempted', 0) or 0),
                        'ocr_backend': _safe_str(pdf_result.get('backend', '')),
                    })
                    return result
                except Exception as exc:  # pragma: no cover - backend dependent
                    last_error = exc
            raise RuntimeError(str(last_error or 'pdf_ocr_backend_unavailable'))

        result['ocr_status'] = 'unsupported'
        result['ocr_error'] = f'unsupported_suffix:{suffix}'
        return result
    except Exception as exc:
        result['ocr_status'] = 'error'
        result['ocr_error'] = str(exc)
        return result


def enrich_with_ocr(
    df: pd.DataFrame,
    scan_root: Path | str,
    config: Optional[OCRConfig] = None,
) -> pd.DataFrame:
    config = config or OCRConfig()
    base = Path(scan_root)
    out = ensure_ocr_input_schema(df)
    candidates = identify_ocr_candidates(out, config)
    candidate_paths = set(candidates['relative_path'].fillna('').astype(str))

    records: List[Dict[str, Any]] = []
    for _, row in out.iterrows():
        relative_path = _safe_str(row.get('relative_path', ''))
        record = {col: '' for col in OCR_RESULT_COLUMNS}
        record['relative_path'] = relative_path
        record['filename'] = _safe_str(row.get('filename', ''))
        record['suffix'] = _safe_str(row.get('suffix', ''))
        record['ocr_candidate'] = relative_path in candidate_paths
        if relative_path not in candidate_paths:
            record['ocr_status'] = 'not_candidate'
            records.append(record)
            continue
        file_path = base / relative_path
        result = ocr_file(file_path, config)
        record.update(result)
        if record['ocr_status'] == 'success' and config.prefer_existing_text:
            existing_text = _safe_str(row.get('extracted_text', ''))
            combined = existing_text
            if existing_text and record['ocr_text'] and record['ocr_text'] not in existing_text:
                combined = (existing_text + '\n\n' + config.ocr_prefix + record['ocr_text']).strip()
            elif not existing_text:
                combined = record['ocr_text']
            record['ocr_text'] = combined[: config.max_output_chars]
            record['ocr_text_preview'] = record['ocr_text'][:1000]
        records.append(record)

    result_df = pd.DataFrame(records)
    if result_df.empty:
        result_df = pd.DataFrame(columns=OCR_RESULT_COLUMNS)
    merged = out.merge(result_df, on=['relative_path', 'filename', 'suffix'], how='left')

    merged['ocr_status'] = merged.get('ocr_status', '').fillna('')
    merged['ocr_error'] = merged.get('ocr_error', '').fillna('')
    merged['ocr_text'] = merged.get('ocr_text', '').fillna('')
    merged['ocr_text_preview'] = merged.get('ocr_text_preview', '').fillna('')
    merged['ocr_source'] = merged.get('ocr_source', '').fillna('')
    merged['ocr_backend'] = merged.get('ocr_backend', '').fillna('')
    merged['ocr_pages_attempted'] = pd.to_numeric(merged.get('ocr_pages_attempted', 0), errors='coerce').fillna(0).astype(int)

    merged['text_after_ocr'] = merged['extracted_text'].fillna('')
    fill_mask = merged['ocr_status'].eq('success') & merged['ocr_text'].fillna('').astype(str).ne('')
    merged.loc[fill_mask, 'text_after_ocr'] = merged.loc[fill_mask, 'ocr_text']
    merged['text_preview_after_ocr'] = merged['text_after_ocr'].fillna('').astype(str).str[:3000]
    merged['ocr_used_for_text'] = fill_mask
    return merged
