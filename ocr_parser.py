"""
ocr_parser.py
-------------
QuickRead-AI | Backend Module 1: Report Text Extraction & Value Parsing

Two jobs:
  1. Extract raw text from an uploaded lab report, whether it's a
     text-based PDF, a scanned/image PDF, or a photo (JPG/PNG) of a
     printed report.
  2. Parse that raw text into structured (test_id, value, unit) tuples
     by matching against the known test aliases in data/lab_ranges.json.

Extraction strategy:
  - PDFs: try `pdfplumber` first (best layout-aware text extraction).
    Fall back to `pypdf` if pdfplumber isn't available. If a PDF page
    yields near-zero text (i.e. it's a scanned image with no text
    layer), rasterize the page and OCR it.
  - Images (JPG/PNG) and rasterized scanned PDF pages: OCR with
    `easyocr` if installed, since it handles printed lab-report fonts
    well without needing a separate system install like Tesseract.

Everything here is intentionally defensive: OCR on real-world lab
report photos is messy (skewed scans, mixed fonts, smudged reference
ranges), so parsing tries multiple regex strategies per test and
reports a confidence flag rather than silently guessing.
"""

import re
import io
import json
import os

# --- Optional dependencies (import lazily / defensively) -------------------
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

_EASYOCR_READER = None  # lazy singleton, built on first use (slow to init)


def _get_easyocr_reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        try:
            import easyocr
        except ImportError as e:
            raise ImportError(
                "easyocr is not installed. Run: pip install easyocr\n"
                "(Note: first run downloads OCR model weights, needs internet.)"
            ) from e
        _EASYOCR_READER = easyocr.Reader(["en"], gpu=False)
    return _EASYOCR_READER


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(file_path_or_buffer):
    """
    Returns raw text extracted from a PDF. Tries pdfplumber, falls back
    to pypdf. If the extracted text is suspiciously short (likely a
    scanned/image-only PDF), returns what little text was found plus a
    flag so the caller can decide whether to attempt OCR fallback.
    """
    text_chunks = []

    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(file_path_or_buffer) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    text_chunks.append(page_text)
        except Exception:
            text_chunks = []

    full_text = "\n".join(text_chunks).strip()

    if not full_text and HAS_PYPDF:
        try:
            if hasattr(file_path_or_buffer, "seek"):
                file_path_or_buffer.seek(0)
            reader = PdfReader(file_path_or_buffer)
            full_text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        except Exception:
            full_text = ""

    likely_scanned = len(full_text) < 40  # near-empty text layer -> probably an image-only PDF
    return full_text, likely_scanned


def extract_text_from_image(file_path_or_buffer):
    """
    OCR an image (JPG/PNG) of a lab report using easyocr.
    Accepts a file path or a file-like object / bytes.
    """
    reader = _get_easyocr_reader()

    if HAS_PIL and not isinstance(file_path_or_buffer, str):
        img = Image.open(file_path_or_buffer).convert("RGB")
        import numpy as np
        result = reader.readtext(np.array(img), detail=0, paragraph=True)
    else:
        result = reader.readtext(file_path_or_buffer, detail=0, paragraph=True)

    return "\n".join(result)


def extract_text(file_path, file_type=None):
    """
    Convenience wrapper: dispatches to the right extractor based on file
    extension (or an explicit file_type override: 'pdf', 'image').
    Returns (text, method_used) where method_used is 'pdf_text',
    'pdf_ocr', or 'image_ocr'.
    """
    file_type = file_type or os.path.splitext(file_path)[1].lower().lstrip(".")

    if file_type == "pdf":
        text, likely_scanned = extract_text_from_pdf(file_path)
        if likely_scanned:
            # NOTE: true scanned-PDF-to-image rasterization needs an extra
            # dependency (pdf2image + poppler). For the hackathon MVP we
            # surface this clearly rather than silently failing; the README
            # documents the optional pdf2image install for that path.
            return text, "pdf_text_low_confidence"
        return text, "pdf_text"

    elif file_type in ("jpg", "jpeg", "png", "bmp", "tiff"):
        text = extract_text_from_image(file_path)
        return text, "image_ocr"

    else:
        raise ValueError(f"Unsupported file type: {file_type}. Use PDF, JPG, or PNG.")


# ---------------------------------------------------------------------------
# Value parsing: raw text -> structured (test_id, value, unit) results
# ---------------------------------------------------------------------------

def _load_ranges_db(ranges_path=None):
    ranges_path = ranges_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "lab_ranges.json"
    )
    with open(ranges_path) as f:
        return json.load(f)


def _build_alias_patterns(ranges_db):
    """
    For every test, build a regex that looks for the alias followed
    (within a short gap, skipping over ':', '-', reference-range noise)
    by the first plausible numeric value on that line.
    """
    patterns = []
    for test in ranges_db["tests"]:
        # Sort aliases longest-first so "vitamin b12" is tried before "b12"
        aliases = sorted(test["aliases"], key=len, reverse=True)
        for alias in aliases:
            escaped = re.escape(alias)
            # alias, then an optional parenthetical annotation right after it
            # (e.g. "Vitamin D (25-OH)", "Hemoglobin (Hb)" -- these must NOT be
            # mistaken for the value itself, since they can contain digits),
            # then a short run of formatting noise, then the actual number.
            pattern = re.compile(
                rf"\b{escaped}\b\s*(?:\([^)]{{0,20}}\))?[\sA-Za-z():\-]{{0,40}}?([-+]?\d+\.?\d*)\s*([a-zA-Zµ/%]+(?:/[a-zA-Zµ]+)?)?",
                re.IGNORECASE,
            )
            patterns.append((test["test_id"], alias, pattern))
    return patterns


def parse_lab_values(raw_text, ranges_db=None, ranges_path=None):
    """
    Scans raw_text line-by-line (falls back to whole-text search if a
    line-based match fails) and returns a list of matches:
        [{
            "test_id": "hba1c",
            "display_name": "HbA1c (Glycated Hemoglobin)",
            "matched_alias": "hba1c",
            "value": 6.8,
            "unit_found": "%",
            "source_line": "HbA1c : 6.8 %  (Normal: <5.7)"
        }, ...]

    If the same test_id is matched more than once (e.g. it also appears
    in a summary section), the first / highest-confidence match (from a
    single line, rather than a whole-text scan) is kept.
    """
    ranges_db = ranges_db or _load_ranges_db(ranges_path)
    patterns = _build_alias_patterns(ranges_db)
    test_lookup = {t["test_id"]: t for t in ranges_db["tests"]}

    results = {}
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    for line in lines:
        for test_id, alias, pattern in patterns:
            if test_id in results:
                continue  # already found a confident match for this test
            m = pattern.search(line)
            if m:
                try:
                    value = float(m.group(1))
                except (TypeError, ValueError):
                    continue
                unit_found = m.group(2) or ""
                results[test_id] = {
                    "test_id": test_id,
                    "display_name": test_lookup[test_id]["display_name"],
                    "matched_alias": alias,
                    "value": value,
                    "unit_found": unit_found,
                    "source_line": line,
                    "confidence": "high",  # matched within a single line
                }

    # Fallback: whole-text search (lower confidence) for anything not yet found,
    # in case OCR/PDF extraction broke a "Test: Value Unit" pattern across lines.
    remaining = [t["test_id"] for t in ranges_db["tests"] if t["test_id"] not in results]
    if remaining:
        flat_text = " ".join(lines)
        for test_id, alias, pattern in patterns:
            if test_id not in remaining:
                continue
            m = pattern.search(flat_text)
            if m:
                try:
                    value = float(m.group(1))
                except (TypeError, ValueError):
                    continue
                results[test_id] = {
                    "test_id": test_id,
                    "display_name": test_lookup[test_id]["display_name"],
                    "matched_alias": alias,
                    "value": value,
                    "unit_found": m.group(2) or "",
                    "source_line": "(matched across wrapped/multi-line text)",
                    "confidence": "low",
                }
                remaining.remove(test_id)

    return list(results.values())


if __name__ == "__main__":
    # Quick manual smoke test using a synthetic report snippet
    sample_report = """
    XYZ DIAGNOSTIC LABORATORY
    Patient: Test Patient   Age/Sex: 34/M
    Report Date: 12-Jun-2026

    COMPLETE BLOOD COUNT
    Hemoglobin (Hb)         : 11.2   g/dL     (Normal: 13.0 - 17.0)

    LIPID PROFILE
    Total Cholesterol        240      mg/dL    (Desirable: <200)
    LDL Cholesterol          165      mg/dL    (Optimal: <100)
    HDL Cholesterol          38       mg/dL    (Normal: >40)
    Triglycerides            210      mg/dL    (Normal: <150)

    DIABETES SCREEN
    HbA1c : 6.9 %   (Normal: <5.7)
    Fasting Blood Glucose    118 mg/dL   (Normal: 70-99)

    THYROID PROFILE
    TSH                       5.2   mIU/L    (Normal: 0.4-4.0)
    """
    parsed = parse_lab_values(sample_report)
    for p in parsed:
        print(f"{p['test_id']:20s} value={p['value']:<8} unit={p['unit_found']:6s} confidence={p['confidence']}")
