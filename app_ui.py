"""
app_ui.py
---------
QuickRead-AI | Frontend: Streamlit Web App

Upload a blood test report (PDF, JPG, or PNG) and get:
  - A color-coded dashboard of each recognized test (Green=Normal,
    Yellow=Borderline, Red=High, Blue=Low)
  - Plain-language explanation of what each test measures
  - General, non-diagnostic advice for out-of-range results
  - An optional spoken summary (English, via gTTS) that can be extended
    to Indian languages via a translation step (see README)

Run with:
    streamlit run frontend/app_ui.py

Dependencies not in the Python standard library:
    pip install streamlit pypdf pdfplumber easyocr pillow plotly gTTS
"""

import os
import sys
import io

import streamlit as st
import plotly.graph_objects as go

# Allow running via `streamlit run frontend/app_ui.py` from the project root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from ocr_parser import extract_text, parse_lab_values
from lab_analyzer import analyze_results, summarize, generate_voice_summary_text

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
RANGES_PATH = os.path.join(DATA_DIR, "lab_ranges.json")

st.set_page_config(
    page_title="QuickRead-AI | Understand Your Lab Report",
    page_icon="🩺",
    layout="wide",
)

STATUS_ORDER = ["high", "low", "borderline", "normal"]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🩺 QuickRead-AI")
st.sidebar.caption("Automated lab report reader & plain-language health advisory")
st.sidebar.markdown("---")

st.sidebar.subheader("Voice summary (optional)")
enable_voice = st.sidebar.checkbox("Generate spoken summary (gTTS)", value=False)
voice_lang = st.sidebar.selectbox(
    "Voice language",
    options=[("en", "English"), ("hi", "Hindi"), ("ta", "Tamil"), ("te", "Telugu"), ("kn", "Kannada")],
    format_func=lambda x: x[1],
) if enable_voice else ("en", "English")

st.sidebar.markdown("---")
st.sidebar.warning(
    "⚠️ **Not a medical diagnosis.** QuickRead-AI compares your numbers to standard "
    "published reference ranges and explains them in plain language. It does not know "
    "your medical history, symptoms, or medications. Always discuss your results with "
    "a doctor."
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

st.title("🩺 QuickRead-AI")
st.caption("Upload a blood test report — get an instant, color-coded, plain-language explanation.")

uploaded_file = st.file_uploader(
    "Upload your lab report (PDF, JPG, or PNG)",
    type=["pdf", "jpg", "jpeg", "png"],
)

demo_col1, demo_col2 = st.columns([1, 3])
use_demo = demo_col1.button("▶️ Try a demo report instead")

DEMO_REPORT_TEXT = """
XYZ DIAGNOSTIC LABORATORY
Patient: Demo Patient   Age/Sex: 42/M

COMPLETE BLOOD COUNT
Hemoglobin (Hb)         : 11.4   g/dL     (Normal: 13.0 - 17.0)

LIPID PROFILE
Total Cholesterol        238      mg/dL    (Desirable: <200)
LDL Cholesterol          158      mg/dL    (Optimal: <100)
HDL Cholesterol          36       mg/dL    (Normal: >40)
Triglycerides            195      mg/dL    (Normal: <150)

DIABETES SCREEN
HbA1c : 6.6 %   (Normal: <5.7)
Fasting Blood Glucose    121 mg/dL   (Normal: 70-99)

THYROID PROFILE
TSH                       5.8   mIU/L    (Normal: 0.4-4.0)

VITAMINS
Vitamin D (25-OH)         17.2  ng/mL
Vitamin B12               265   pg/mL
"""

raw_text = None
extraction_method = None

if uploaded_file is not None:
    suffix = uploaded_file.name.split(".")[-1].lower()
    with st.spinner("Reading your report..."):
        try:
            # extract_text expects a path OR file-like object depending on branch;
            # for uploaded PDFs pdfplumber/pypdf both accept a file-like buffer directly.
            if suffix == "pdf":
                from ocr_parser import extract_text_from_pdf
                raw_text, likely_scanned = extract_text_from_pdf(io.BytesIO(uploaded_file.getvalue()))
                extraction_method = "pdf_text_low_confidence" if likely_scanned else "pdf_text"
            else:
                from ocr_parser import extract_text_from_image
                raw_text = extract_text_from_image(io.BytesIO(uploaded_file.getvalue()))
                extraction_method = "image_ocr"
        except ImportError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(f"Couldn't read this file: {e}")
            st.stop()

elif use_demo:
    raw_text = DEMO_REPORT_TEXT
    extraction_method = "demo"

if raw_text is not None:
    if extraction_method == "pdf_text_low_confidence":
        st.warning(
            "This looks like a scanned/image-only PDF with little to no extractable text. "
            "Results below may be incomplete. For best results, upload a clear photo (JPG/PNG) "
            "of the report instead, or a text-based PDF."
        )

    with st.spinner("Analyzing test values..."):
        parsed = parse_lab_values(raw_text, ranges_path=RANGES_PATH)
        results = analyze_results(parsed, ranges_path=RANGES_PATH)
        summary = summarize(results)

    if not results:
        st.error(
            "No recognizable lab test values were found in this report. "
            "This can happen if the report uses different test names than QuickRead-AI "
            "currently recognizes, or if the scan quality is poor."
        )
        with st.expander("Show raw extracted text (for debugging)"):
            st.text(raw_text)
        st.stop()

    # ---------------- Headline summary ----------------
    st.markdown("## Summary")
    if summary["counts_by_status"]["high"] + summary["counts_by_status"]["low"] == 0:
        st.success(summary["headline"])
    else:
        st.info(summary["headline"])

    kpi_cols = st.columns(4)
    kpi_cols[0].metric("🟢 Normal", summary["counts_by_status"]["normal"])
    kpi_cols[1].metric("🟡 Borderline", summary["counts_by_status"]["borderline"])
    kpi_cols[2].metric("🔴 High", summary["counts_by_status"]["high"])
    kpi_cols[3].metric("🔵 Low", summary["counts_by_status"]["low"])

    # ---------------- Visual dashboard ----------------
    st.markdown("## Your Results")

    status_colors = {"normal": "#2ecc71", "borderline": "#f1c40f", "high": "#e74c3c", "low": "#3498db"}
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=[r["display_name"] for r in results],
        x=[r["value"] for r in results],
        orientation="h",
        marker_color=[status_colors[r["status"]] for r in results],
        text=[f"{r['value']} {r['unit']}" for r in results],
        textposition="outside",
    ))
    fig.update_layout(
        height=max(300, 45 * len(results)),
        xaxis_title="Value (each test's own unit — see cards below for ranges)",
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ---------------- Detail cards ----------------
    for r in results:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"### {r['emoji']} {r['display_name']}")
                st.caption(r["category"])
            with c2:
                st.markdown(
                    f"<h2 style='text-align:right; color:{r['color']};'>{r['value']} {r['unit']}</h2>"
                    f"<p style='text-align:right;'><b>{r['status_label']}</b> ({r['band_label']})</p>",
                    unsafe_allow_html=True,
                )
            st.write(f"**What this measures:** {r['plain_language']}")
            st.write(f"**What this result means:** {r['advice_text']}")
            if r["confidence"] == "low":
                st.caption("⚠️ Lower-confidence match — please verify this value against your original report.")

    # ---------------- Voice summary ----------------
    if enable_voice:
        st.markdown("## 🔊 Spoken Summary")
        voice_text_en = generate_voice_summary_text(results)

        try:
            from gtts import gTTS
        except ImportError:
            st.error("gTTS is not installed. Run: pip install gTTS")
        else:
            lang_code = voice_lang[0]
            spoken_text = voice_text_en
            translated_ok = True

            if lang_code != "en":
                try:
                    from deep_translator import GoogleTranslator
                except ImportError:
                    st.error(
                        "The translation library isn't installed, so the summary can't be "
                        "translated before speaking. Run: pip install deep-translator\n\n"
                        "Falling back to English audio for now."
                    )
                    translated_ok = False
                else:
                    with st.spinner(f"Translating summary to {voice_lang[1]}..."):
                        try:
                            # deep-translator's GoogleTranslator has a ~5000-char limit per
                            # call, well above our short summary, so one call is enough here.
                            spoken_text = GoogleTranslator(source="en", target=lang_code).translate(voice_text_en)
                        except Exception as e:
                            st.warning(
                                f"Couldn't translate to {voice_lang[1]} ({e}). "
                                f"Falling back to English audio."
                            )
                            spoken_text = voice_text_en
                            translated_ok = False

                if translated_ok:
                    with st.expander(f"Show translated text ({voice_lang[1]})"):
                        st.write(spoken_text)

            with st.spinner("Generating audio..."):
                try:
                    tts_lang = lang_code if (lang_code == "en" or translated_ok) else "en"
                    tts = gTTS(text=spoken_text, lang=tts_lang)
                    audio_buffer = io.BytesIO()
                    tts.write_to_fp(audio_buffer)
                    audio_buffer.seek(0)
                    st.audio(audio_buffer, format="audio/mp3")
                except Exception as e:
                    st.error(
                        f"Couldn't generate audio: {e}\n\n"
                        f"If this says the language isn't supported, gTTS may not support "
                        f"this code directly -- check the gTTS docs for supported language codes."
                    )

    with st.expander("Show raw extracted text"):
        st.text(raw_text)

else:
    st.info("👆 Upload a lab report above, or click **Try a demo report** to see QuickRead-AI in action.")

st.markdown("---")
st.caption(
    "QuickRead-AI is a health-literacy tool, not a diagnostic device. Reference ranges are "
    "general adult values and can vary by lab, age, sex, and individual context. Always "
    "discuss your results with a qualified doctor."
)
