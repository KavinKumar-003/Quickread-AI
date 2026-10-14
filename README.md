# QuickRead-AI

Automated medical lab report parser & plain-language health advisory tool.
Upload a blood test PDF/photo → get a color-coded dashboard explaining what
each number means, in plain language, with a "discuss with your doctor"
nudge on anything outside the normal range.

## Folder structure

```
QuickRead-AI/
├── data/
│   └── lab_ranges.json       # 12 common lab tests: reference ranges, plain-language
│                              # explanations, and general advice text
├── backend/
│   ├── __init__.py
│   ├── ocr_parser.py         # PDF/image text extraction + regex value parsing
│   └── lab_analyzer.py       # classifies values against ranges, builds summaries
├── frontend/
│   └── app_ui.py             # Streamlit web app (run this)
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
```

Note: `easyocr`'s first run downloads OCR model weights (~64MB), which needs
an internet connection once. After that it works offline.

## Run

```bash
streamlit run frontend/app_ui.py
```

Opens at `http://localhost:8501`. Click **"Try a demo report"** for an
instant walkthrough with no upload needed, or upload a real PDF/JPG/PNG.

## What's tested vs. what needs your own testing

Built and verified in this environment:
- `data/lab_ranges.json` — valid JSON, 12 tests loaded correctly
- `ocr_parser.py` — regex value-parsing tested against both a clean
  synthetic report and a messy/lowercase one; correctly extracted
  hemoglobin, cholesterol panel, HbA1c, glucose, TSH, vitamin D/B12,
  creatinine, and ALT in both cases
- `lab_analyzer.py` — classification, sorting (attention-needed items
  first), and voice-summary text generation all verified against both an
  abnormal and a fully-normal sample report

**Not execution-tested here** (this sandbox has no internet access, so
`streamlit`, `easyocr`, and `gTTS` couldn't be installed):
- `frontend/app_ui.py` — syntax-checked and logically complete, but run it
  locally to confirm the Streamlit UI behaves as expected
- Actual OCR on a real scanned image (the `easyocr` code path)
- Actual audio generation via `gTTS`

If either throws an error on your machine, paste the message back and I'll
fix it.

## Scope & honesty notes (good to mention in your SIH pitch)

- **This is a health-literacy tool, not a diagnostic device.** It compares
  a number to a *standard, published* reference range — the same kind
  printed on the side of any lab report — and explains it in plain
  language. It does not know the patient's history, symptoms, or
  medications, and every result explicitly says to consult a doctor.
- Reference ranges in `lab_ranges.json` are general adult ranges. Real labs
  print slightly different ranges depending on the instrument/method, and
  some tests (like hemoglobin) have separate ranges for men and women —
  the app currently uses one general range and says so.
- OCR/PDF parsing on real-world reports is messy (skewed scans, unusual
  layouts, non-standard test names). The parser handles a lot of variation
  via regex + a wide alias list, and reports a "low confidence" flag when
  it had to guess — but no OCR pipeline is 100% reliable, so the UI shows
  the raw extracted text for user verification.
- The voice-summary feature now translates the English summary into the
  selected language (Hindi/Tamil/Telugu/Kannada) via `deep-translator`
  (a free wrapper around Google Translate's web endpoint) before handing
  it to gTTS, so the audio is actually spoken in that language, not just
  read in English. Requires internet at runtime (same as gTTS itself).
  If translation fails for any reason (rate limiting, network issue), the
  app shows a warning and falls back to English audio rather than
  crashing.

## Extending for the real hackathon submission

- Add more tests to `data/lab_ranges.json` (it's a simple, well-structured
  format — CBC differential, kidney panel (BUN, eGFR), liver panel (AST,
  bilirubin, ALP), electrolytes, etc.)
- Add sex-specific and age-specific reference ranges where they matter
  (hemoglobin, creatinine).
- Wire in a real translation step for true vernacular voice summaries.
- Add a "download as PDF" plain-language report (reuse the reportlab
  pattern from GovAudit-AI if you built that too).
- For true scanned-PDF support (not just text-PDF or JPG/PNG), add
  `pdf2image` + Poppler to rasterize scanned PDF pages before OCR.
