"""
Builds the final sample_data/*.csv files used by merge_and_load.py.

sample_data/raw/*.csv are verbatim copies of the real exports you shared
(untouched -- Bitrix's really is ~164 columns; Zoom's really is 7). This
script loads those, appends a few clearly-marked SYNTHETIC rows so the
merge/de-dupe logic has more to exercise (a plain unique lead per source,
plus a fuzzy-name match with no email/phone in common), and writes the
result to sample_data/*.csv -- keeping every original column so the loader
in merge_and_load.py is tested against the same "way more than 5 columns"
shape the real files have.

Run once to (re)build sample_data/*.csv:
    python scripts/generate_sample_data.py
"""

from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "sample_data" / "raw"
OUT_DIR = BASE_DIR / "sample_data"

ZOOM_RAW = RAW_DIR / "Zoom Registrations sample Data.csv"
BITRIX_RAW = RAW_DIR / "Bitrix Sample Data.csv"

# Not in the real export -- added purely to exercise merge_and_load.py's
# de-dupe logic: one plain unique lead, and one lead ("Vijay Reddy") that
# shares no email/phone with anyone but should still fuzzy-match Zoom's
# real "Vijay Reddi" row on name alone.
ZOOM_SYNTHETIC_ROWS = [
    {
        "First Name": "Priya",
        "Last Name": "Nair",
        "Email": "priya.nair@example.com",
        "Registration Time": "9/3/2026 20:15",
        "Approval Status": "approved",
        "Phone": "2065551234",
        "How did you hear about this webinar?": "LinkedIn Ad",
    },
]

BITRIX_SYNTHETIC_ROWS = [
    {
        "First Name": "Lena",
        "Last Name": "Brooks",
        "Created": "08.09.2026 14:20:10 pm",
        "Source": "WEB FORM",
        "Work Phone": "4257778888",
        "Work E-mail": "lena.brooks@example.com",
        "Comment": "Requested pricing sheet",
    },
    {
        # No email/phone overlap with Zoom's "Vijay Reddi" -- only a fuzzy
        # name match should merge these two.
        "First Name": "Vijay",
        "Last Name": "Reddy",
        "Created": "10.09.2026 11:05:00 am",
        "Source": "AI CALLING",
        "Work Phone": "4259990000",
        "Work E-mail": "vijay.reddi@work-example.com",
        "Comment": "Asked about enterprise pricing",
    },
]


def _build(raw_path: Path, synthetic_rows: list[dict], out_name: str) -> None:
    base = pd.read_csv(raw_path, dtype=str)
    synthetic = pd.DataFrame(synthetic_rows)
    combined = pd.concat([base, synthetic], ignore_index=True)
    combined = combined.reindex(columns=base.columns)  # keep original column order/width
    path = OUT_DIR / out_name
    combined.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"wrote {path} ({len(combined)} rows, {len(combined.columns)} columns)")


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _build(ZOOM_RAW, ZOOM_SYNTHETIC_ROWS, "Zoom Registrations sample Data.csv")
    _build(BITRIX_RAW, BITRIX_SYNTHETIC_ROWS, "Bitrix Sample Data.csv")
