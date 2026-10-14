"""
lab_analyzer.py
----------------
QuickRead-AI | Backend Module 2: Health Status & Plain-Language Advisory Engine

Takes the structured (test_id, value) results from ocr_parser.py and:
  1. Classifies each value against the reference ranges in
     data/lab_ranges.json (normal / borderline / high / low)
  2. Attaches the plain-language explanation and general advice text
     for that status
  3. Produces an overall report summary (counts by status, a simple
     "how many things need attention" headline)

IMPORTANT SCOPE NOTE: This module compares a number to a published
reference range and surfaces the same general, publicly available
health information a patient could read off the side of their own lab
report. It does NOT diagnose any condition, does NOT account for a
patient's individual history/symptoms/medications, and every output
explicitly recommends discussing results with a doctor. It is a
health-literacy aid, not a substitute for medical advice.
"""

import json
import os


def _load_ranges_db(ranges_path=None):
    ranges_path = ranges_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "lab_ranges.json"
    )
    with open(ranges_path) as f:
        return json.load(f)


def classify_value(value, test_def):
    """
    Given a numeric value and a test definition (from lab_ranges.json),
    return the matching range band: {"label", "status", "min", "max"}.
    If the value doesn't fall cleanly into any band (shouldn't normally
    happen since bands should be contiguous), falls back to the nearest band.
    """
    bands = test_def["ranges"]
    for band in bands:
        if band["min"] <= value <= band["max"]:
            return band

    # Fallback: nearest band by distance (defensive, in case of gaps/typos in ranges)
    def distance(band):
        if value < band["min"]:
            return band["min"] - value
        if value > band["max"]:
            return value - band["max"]
        return 0

    return min(bands, key=distance)


STATUS_DISPLAY = {
    "normal": {"label": "Normal", "color": "#2ecc71", "emoji": "🟢"},
    "borderline": {"label": "Borderline", "color": "#f1c40f", "emoji": "🟡"},
    "high": {"label": "High", "color": "#e74c3c", "emoji": "🔴"},
    "low": {"label": "Low", "color": "#3498db", "emoji": "🔵"},
}


def analyze_results(parsed_values, ranges_db=None, ranges_path=None):
    """
    parsed_values: list of dicts from ocr_parser.parse_lab_values(), each
    containing at least {"test_id", "value"}.

    Returns a list of enriched result dicts ready for display:
        {
            "test_id", "display_name", "category", "value", "unit",
            "band_label", "status", "status_display", "color", "emoji",
            "plain_language", "advice_text", "confidence"
        }
    """
    ranges_db = ranges_db or _load_ranges_db(ranges_path)
    test_lookup = {t["test_id"]: t for t in ranges_db["tests"]}

    enriched = []
    for pv in parsed_values:
        test_def = test_lookup.get(pv["test_id"])
        if not test_def:
            continue

        band = classify_value(pv["value"], test_def)
        status = band["status"]
        display = STATUS_DISPLAY.get(status, {"label": status.title(), "color": "#7f8c8d", "emoji": "⚪"})
        advice_text = test_def["advice"].get(status, "Discuss this result with a doctor for personalized guidance.")

        enriched.append({
            "test_id": pv["test_id"],
            "display_name": test_def["display_name"],
            "category": test_def["category"],
            "value": pv["value"],
            "unit": test_def.get("unit", pv.get("unit_found", "")),
            "band_label": band["label"],
            "status": status,
            "status_label": display["label"],
            "color": display["color"],
            "emoji": display["emoji"],
            "plain_language": test_def["plain_language"],
            "advice_text": advice_text,
            "confidence": pv.get("confidence", "high"),
            "source_line": pv.get("source_line", ""),
        })

    # Sort so attention-needed items surface first: high/low, then borderline, then normal
    priority = {"high": 0, "low": 1, "borderline": 2, "normal": 3}
    enriched.sort(key=lambda r: priority.get(r["status"], 4))
    return enriched


def summarize(enriched_results):
    """
    Produces a short overview: counts per status + a one-line headline.
    """
    counts = {"normal": 0, "borderline": 0, "high": 0, "low": 0}
    for r in enriched_results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    total = len(enriched_results)
    needs_attention = counts["high"] + counts["low"] + counts["borderline"]

    if total == 0:
        headline = "No recognizable lab values were found in this report."
    elif needs_attention == 0:
        headline = f"All {total} recognized test(s) are within the normal range. 🎉"
    else:
        headline = (
            f"{needs_attention} of {total} recognized test(s) fall outside the typical range "
            f"and are worth discussing with a doctor."
        )

    return {
        "total_tests_found": total,
        "counts_by_status": counts,
        "headline": headline,
    }


def generate_voice_summary_text(enriched_results, language="en"):
    """
    Builds a short, spoken-friendly plain-language summary suitable for
    passing to a text-to-speech engine (e.g. gTTS) for a vernacular voice
    readout. Kept deliberately simple and non-alarming in tone.

    `language` is accepted here for interface consistency with the
    frontend (which picks the gTTS language code separately) -- the
    English summary text itself is generated here; translation to other
    Indian languages should go through a translation step before TTS
    (see frontend/app_ui.py for how this is wired up, and the README for
    the current MVP's language scope).
    """
    if not enriched_results:
        return "No test results could be read from this report. Please try uploading a clearer copy."

    summary = summarize(enriched_results)
    lines = [summary["headline"]]

    attention_items = [r for r in enriched_results if r["status"] != "normal"]
    for item in attention_items[:5]:  # keep the spoken summary short
        lines.append(
            f"Your {item['display_name']} is {item['status_label'].lower()}, "
            f"at {item['value']} {item['unit']}."
        )

    lines.append("Please remember, this is general information only. Please consult a doctor to discuss these results.")
    return " ".join(lines)


if __name__ == "__main__":
    # Smoke test using the same sample as ocr_parser.py
    from ocr_parser import parse_lab_values

    sample_report = """
    Hemoglobin (Hb)         : 11.2   g/dL     (Normal: 13.0 - 17.0)
    Total Cholesterol        240      mg/dL    (Desirable: <200)
    LDL Cholesterol          165      mg/dL    (Optimal: <100)
    HDL Cholesterol          38       mg/dL    (Normal: >40)
    HbA1c : 6.9 %   (Normal: <5.7)
    TSH                       5.2   mIU/L    (Normal: 0.4-4.0)
    """
    parsed = parse_lab_values(sample_report)
    results = analyze_results(parsed)
    summary = summarize(results)

    print("SUMMARY:", summary["headline"])
    print(json.dumps(summary["counts_by_status"], indent=2))
    print()
    for r in results:
        print(f"{r['emoji']} {r['display_name']:35s} {r['value']:>6} {r['unit']:8s} -> {r['status_label']}")
        print(f"   {r['advice_text']}")
        print()

    print("--- VOICE SUMMARY TEXT ---")
    print(generate_voice_summary_text(results))
