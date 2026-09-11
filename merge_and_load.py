"""
Pulls lead data out of the Zoom registration export and the Bitrix CRM
export, reduces each to the 5 columns the destination table cares about,
merges + de-dupes them, and loads the result into a SQL Server table.

Destination schema (5 columns):
    first_name, last_name, email, phone, comment

The real exports are far wider than 5 columns (Bitrix's is ~164 columns)
and can carry a few metadata/title rows above the real header row, so
loading is defensive about both: _read_source_table() scans the first few
rows of the file to find the one that actually looks like a header before
reading the table, and _resolve_columns() maps destination fields to source
columns by exact header match first (falling back to substring match only
if nothing matches exactly) so it doesn't get fooled by look-alike columns
(e.g. Bitrix has "Comment" *and* "Instagram comments"; "First Name" *and*
"Shareholder 1 First Name").

Today the sources are the two sample .csv files in sample_data/. Later,
swap load_zoom_registrations()/load_bitrix_contacts() for API calls (Zoom
API, Bitrix24 REST API) -- everything downstream (normalize/merge/load)
stays the same.

Usage:
    # Dry run against a local SQLite file (default -- no AWS creds needed):
    python merge_and_load.py

    # Real run against AWS SQL Server, once creds are set as env vars
    # (see .env.example):
    python merge_and_load.py --target sqlserver
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Unicode,
    UnicodeText,
    create_engine,
    func,
    text,
)
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = BASE_DIR / "sample_data"
ZOOM_FILE = SAMPLE_DIR / "Zoom Registrations sample Data.csv"
BITRIX_FILE = SAMPLE_DIR / "Bitrix Sample Data.csv"

DEST_TABLE = "webinar_leads"

# How many leading rows to scan when looking for the real header row (in
# case a file has title/export-info rows above it).
HEADER_SCAN_ROWS = 15

# ---------------------------------------------------------------------------
# Source -> destination column mapping.
# Real exports won't always spell headers identically, so each dest field
# lists hint phrases in priority order. _resolve_columns() tries an EXACT
# (punctuation/case-insensitive) match against each phrase first -- that's
# what correctly picks "Work E-mail" over "Home E-mail"/"Other E-mail", and
# plain "First Name" over "Shareholder 1 First Name" -- and only falls back
# to substring matching if no phrase matches exactly anywhere.
# ---------------------------------------------------------------------------

ZOOM_COLUMN_HINTS = {
    "first_name": ["first name"],
    "last_name": ["last name"],
    "email": ["email"],
    "phone": ["phone"],
    "comment": ["how did you hear about this webinar", "how did you hear"],
}

BITRIX_COLUMN_HINTS = {
    "first_name": ["first name"],
    "last_name": ["last name"],
    "email": ["work e-mail", "work email", "email"],
    "phone": ["work phone", "phone"],
    "comment": ["comment"],
}


def _normalize_header(header: str) -> str:
    """Lowercases and strips ALL non-alphanumeric characters (no spaces
    left behind), so e.g. "Work E-mail" and "work email" normalize to the
    same "workemail" and compare equal."""
    return re.sub(r"[^a-z0-9]+", "", str(header).lower())


def _resolve_columns(columns: list[str], hints: dict[str, list[str]]) -> dict[str, str]:
    """Maps each destination field to a source column: an exact header
    match (checked in hint-phrase priority order) if one exists, otherwise
    the first column containing that phrase. Each source column is used for
    at most one destination field."""
    normalized = {col: _normalize_header(col) for col in columns}
    resolved: dict[str, str] = {}
    used: set[str] = set()

    for dest_field, phrases in hints.items():
        norm_phrases = [_normalize_header(p) for p in phrases]
        match: str | None = None

        for phrase in norm_phrases:
            match = next(
                (col for col, norm in normalized.items() if col not in used and norm == phrase),
                None,
            )
            if match:
                break

        if not match:
            for phrase in norm_phrases:
                match = next(
                    (col for col, norm in normalized.items() if col not in used and phrase in norm),
                    None,
                )
                if match:
                    break

        if not match:
            raise ValueError(
                f"Could not find a source column for '{dest_field}' "
                f"(looked for {phrases}) among columns: {columns}"
            )
        resolved[dest_field] = match
        used.add(match)
    return resolved


def _score_header_candidate(cells: list[str], hint_words: list[str]) -> int:
    normalized_cells = [_normalize_header(c) for c in cells if c is not None and str(c).strip()]
    return sum(1 for word in hint_words if any(word in cell for cell in normalized_cells))


def _read_source_table(path: Path, header_hint_words: list[str]) -> pd.DataFrame:
    """Reads a CSV/XLSX export that may have metadata/title rows above the
    real header row: scans the first HEADER_SCAN_ROWS rows, scores each one
    by how many header_hint_words it contains, and reads the table starting
    from the best-scoring row (defaulting to row 0 if nothing scores).

    CSV rows are scanned with the csv module rather than pandas, because
    stray title rows are typically ragged (fewer fields than the real
    header) and pandas' C parser refuses to even preview a file with
    inconsistent field counts per row.
    """
    hint_words = [_normalize_header(w) for w in header_hint_words]

    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            preview_rows = [row for _, row in zip(range(HEADER_SCAN_ROWS), csv.reader(f))]
        scores = [_score_header_candidate(row, hint_words) for row in preview_rows]
        header_row = max(range(len(scores)), key=lambda i: scores[i]) if scores else 0
        return pd.read_csv(path, header=header_row, dtype=str, encoding="utf-8-sig")

    preview = pd.read_excel(path, header=None, nrows=HEADER_SCAN_ROWS, dtype=str)
    scores = [_score_header_candidate(preview.iloc[i].tolist(), hint_words) for i in range(len(preview))]
    header_row = max(range(len(scores)), key=lambda i: scores[i]) if scores else 0
    return pd.read_excel(path, header=header_row, dtype=str)


def normalize_phone(value: object) -> str | None:
    if pd.isna(value):
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def normalize_email(value: object) -> str | None:
    if pd.isna(value):
        return None
    email = str(value).strip().lower()
    return email or None


def normalize_name(value: object) -> str | None:
    if pd.isna(value):
        return None
    name = str(value).strip()
    return name or None


def _standardize(df: pd.DataFrame, hints: dict[str, list[str]], source_system: str) -> pd.DataFrame:
    col_map = _resolve_columns(list(df.columns), hints)
    out = pd.DataFrame(
        {
            "first_name": df[col_map["first_name"]].map(normalize_name),
            "last_name": df[col_map["last_name"]].map(normalize_name),
            "email": df[col_map["email"]].map(normalize_email),
            "phone": df[col_map["phone"]].map(normalize_phone),
            "comment": df[col_map["comment"]].map(normalize_name),
        }
    )
    out["source_system"] = source_system
    return out


def load_zoom_registrations(path: Path = ZOOM_FILE) -> pd.DataFrame:
    """Reads the Zoom registration export. Replace with a call to the Zoom
    API (e.g. GET /webinars/{id}/registrants) once that's wired up --
    just return a DataFrame with the same raw column names this reads today."""
    raw = _read_source_table(path, header_hint_words=["first name", "last name", "email"])
    return _standardize(raw, ZOOM_COLUMN_HINTS, source_system="zoom")


def load_bitrix_contacts(path: Path = BITRIX_FILE) -> pd.DataFrame:
    """Reads the Bitrix CRM export. Replace with a call to the Bitrix24 REST
    API (e.g. crm.contact.list) once that's wired up."""
    raw = _read_source_table(path, header_hint_words=["first name", "last name", "work e-mail"])
    return _standardize(raw, BITRIX_COLUMN_HINTS, source_system="bitrix")


# Similarity ratio (difflib SequenceMatcher, 0-1) above which two leads'
# names are considered "close enough" to be the same person, when their
# contact info alone doesn't already prove it. Tune this if you see
# too many/few fuzzy merges in real data -- 0.90 tolerates a couple of
# typo'd characters but won't match unrelated people with common names.
NAME_MATCH_THRESHOLD = 0.90


class _UnionFind:
    """Tiny union-find so a lead can be linked into the same cluster via
    *either* an exact email/phone match *or* a fuzzy name match, and those
    links chain transitively (A~B via email, B~C via name -> A,B,C grouped)."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _full_name_key(row: pd.Series) -> str:
    return _normalize_header(f"{row['first_name'] or ''} {row['last_name'] or ''}")


def merge_sources(*frames: pd.DataFrame) -> pd.DataFrame:
    """Concatenates all sources and collapses duplicate leads.

    Two rows are treated as the same lead if either:
      - they share the same email, or the same phone number (exact match), or
      - neither of the above is available/matching, but their names are a
        close fuzzy match (see NAME_MATCH_THRESHOLD) -- this covers the
        common case of the same person showing up with a work email/phone
        in one source and a personal email/phone in the other, and their
        name typed slightly differently by whoever entered it.

    Kept row combines all distinct comments from the rows it absorbs, and
    records *why* they were merged in `match_reason` for a human to audit.
    """
    combined = pd.concat(frames, ignore_index=True)
    n = len(combined)
    uf = _UnionFind(n)

    # 1) Exact match: same email, or same phone.
    for col in ("email", "phone"):
        first_seen: dict[str, int] = {}
        for idx, value in combined[col].items():
            if not value:
                continue
            if value in first_seen:
                uf.union(idx, first_seen[value])
            else:
                first_seen[value] = idx

    # 2) Fuzzy match: names close enough, for rows not already linked.
    full_names = combined.apply(_full_name_key, axis=1)
    for i in range(n):
        if not full_names[i]:
            continue
        for j in range(i + 1, n):
            if not full_names[j] or uf.find(i) == uf.find(j):
                continue
            if SequenceMatcher(None, full_names[i], full_names[j]).ratio() >= NAME_MATCH_THRESHOLD:
                uf.union(i, j)

    combined["_cluster"] = [uf.find(i) for i in range(n)]

    def _match_reason(group: pd.DataFrame) -> str:
        if len(group) == 1:
            return "unique"
        reasons = []
        if group["email"].dropna().duplicated().any():
            reasons.append("email")
        if group["phone"].dropna().duplicated().any():
            reasons.append("phone")
        if not reasons:
            reasons.append("fuzzy_name")
        return "+".join(reasons)

    def _first_non_null(series: pd.Series):
        non_null = series.dropna()
        return non_null.iloc[0] if len(non_null) else None

    def _collapse(group: pd.DataFrame) -> pd.Series:
        # Prefer the first non-blank value per field across the whole
        # cluster, not just whatever happened to be in the first row --
        # e.g. one duplicate record may have an email while another (of the
        # same person) doesn't.
        first = group.iloc[0].copy()
        for field in ("first_name", "last_name", "email", "phone"):
            first[field] = _first_non_null(group[field])
        comments = [c for c in group["comment"].dropna().unique() if c]
        first["comment"] = " | ".join(comments) if comments else None
        first["source_system"] = ", ".join(sorted(group["source_system"].unique()))
        first["match_reason"] = _match_reason(group)
        return first

    deduped = (
        combined.groupby("_cluster", sort=False)
        .apply(_collapse, include_groups=False)
        .reset_index(drop=True)
    )
    return deduped[
        ["first_name", "last_name", "email", "phone", "comment", "source_system", "match_reason"]
    ]


# ---------------------------------------------------------------------------
# Loading into SQL Server (or a local SQLite stand-in for dry runs)
# ---------------------------------------------------------------------------


def get_engine(target: str) -> Engine:
    if target == "sqlserver":
        server = os.environ["AWS_SQL_SERVER"]  # e.g. mydb.abc123.us-east-1.rds.amazonaws.com,1433
        database = os.environ["AWS_SQL_DATABASE"]
        username = os.environ["AWS_SQL_USERNAME"]
        password = os.environ["AWS_SQL_PASSWORD"]
        driver = os.environ.get("AWS_SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
        conn_str = (
            f"mssql+pyodbc://{username}:{password}@{server}/{database}"
            f"?driver={driver.replace(' ', '+')}"
        )
        return create_engine(conn_str)

    # Local dry-run target: a SQLite file next to this script, so the whole
    # pipeline is runnable end to end with zero AWS access.
    db_path = BASE_DIR / "test_output.db"
    return create_engine(f"sqlite:///{db_path}")


def _ensure_scd2_columns(engine: Engine, table_name: str) -> None:
    """Adds the SCD-2 monitoring columns to the destination table.

    Existing rows are backfilled as current version 1 so a later re-run can
    close the prior version and insert a new one only when the data changed.
    """
    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("first_name", Unicode(100)),
        Column("last_name", Unicode(100)),
        Column("email", Unicode(255)),
        Column("phone", Unicode(30)),
        Column("comment", UnicodeText),
        Column("loaded_at", DateTime, nullable=False, server_default=func.now()),
        Column("effective_start", DateTime),
        Column("effective_end", DateTime),
        Column("is_current", Integer),
        Column("version", Integer),
    )
    table.create(bind=engine, checkfirst=True)

    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            existing_columns = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            }

            for column_name, column_sql in (
                ("effective_start", "DATETIME"),
                ("effective_end", "DATETIME"),
                ("is_current", "INTEGER"),
                ("version", "INTEGER"),
            ):
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))

            conn.execute(
                text(
                    f"""
                    UPDATE {table_name}
                    SET
                        effective_start = COALESCE(effective_start, loaded_at),
                        effective_end = NULL,
                        is_current = COALESCE(is_current, 1),
                        version = COALESCE(version, 1)
                    WHERE effective_start IS NULL OR is_current IS NULL OR version IS NULL
                    """
                )
            )
        return

    with engine.begin() as conn:
        existing_columns = {
            row[0]
            for row in conn.execute(
                text(
                    f"""
                    SELECT c.name
                    FROM sys.columns c
                    INNER JOIN sys.tables t ON c.object_id = t.object_id
                    WHERE t.name = '{table_name}'
                    """
                )
            ).fetchall()
        }

        for column_name, column_sql in (
            ("effective_start", "DATETIME2(0) NULL"),
            ("effective_end", "DATETIME2(0) NULL"),
            ("is_current", "INT NULL"),
            ("version", "INT NULL"),
        ):
            if column_name not in existing_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE dbo.{table_name} ADD {column_name} {column_sql}"
                    )
                )

        conn.execute(
            text(
                f"""
                UPDATE dbo.{table_name}
                SET
                    effective_start = COALESCE(effective_start, loaded_at),
                    effective_end = NULL,
                    is_current = COALESCE(is_current, 1),
                    version = COALESCE(version, 1)
                WHERE effective_start IS NULL OR is_current IS NULL OR version IS NULL
                """
            )
        )


def _row_signature(row: dict[str, object]) -> dict[str, object]:
    return {
        "first_name": None if pd.isna(row.get("first_name")) else str(row["first_name"]).strip(),
        "last_name": None if pd.isna(row.get("last_name")) else str(row["last_name"]).strip(),
        "email": None if pd.isna(row.get("email")) else str(row["email"]).strip().lower(),
        "phone": None if pd.isna(row.get("phone")) else str(row["phone"]).strip(),
        "comment": None if pd.isna(row.get("comment")) else str(row["comment"]).strip(),
    }


def _current_match(existing_rows: pd.DataFrame, row: dict[str, object]) -> pd.Series | None:
    if not isinstance(existing_rows, pd.DataFrame) or existing_rows.empty:
        return None

    signature = _row_signature(row)

    if signature["email"]:
        matches = existing_rows[
            (existing_rows["email"].fillna("").str.lower() == signature["email"])
            & (existing_rows["is_current"].fillna(1) == 1)
        ]
    else:
        matches = existing_rows[
            (existing_rows["phone"].fillna("") == (signature["phone"] or ""))
            & (existing_rows["is_current"].fillna(1) == 1)
        ]

    if matches.empty:
        return None

    return matches.iloc[0]


def _rows_equal(current_row: pd.Series, incoming_row: dict[str, object]) -> bool:
    incoming = _row_signature(incoming_row)
    for field in ("first_name", "last_name", "email", "phone", "comment"):
        current_value = current_row.get(field)
        if pd.isna(current_value):
            current_value = None
        else:
            current_value = str(current_value).strip()
            if field == "email":
                current_value = current_value.lower()
        if current_value != incoming[field]:
            return False
    return True


def push_to_sql_server(df: pd.DataFrame, engine: Engine, table_name: str = DEST_TABLE) -> None:
    """Creates the destination table if it doesn't exist yet and stores rows
    using SCD-2 semantics. Existing rows are versioned and updated only when
    a later load changes the lead's data.
    """
    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("first_name", Unicode(100)),
        Column("last_name", Unicode(100)),
        Column("email", Unicode(255)),
        Column("phone", Unicode(30)),
        Column("comment", UnicodeText),
        Column("loaded_at", DateTime, nullable=False, server_default=func.now()),
        Column("effective_start", DateTime),
        Column("effective_end", DateTime),
        Column("is_current", Integer),
        Column("version", Integer),
    )
    metadata.create_all(engine)
    _ensure_scd2_columns(engine, table_name)

    table_ref = f"dbo.{table_name}" if engine.dialect.name == "mssql" else table_name
    existing_rows = pd.read_sql(
        f"SELECT * FROM {table_ref} WHERE is_current = 1 OR is_current IS NULL",
        engine,
    )

    payload = df[["first_name", "last_name", "email", "phone", "comment"]].to_dict(orient="records")
    now = datetime.now(timezone.utc)

    updates: list[tuple[int, datetime]] = []
    inserts: list[dict[str, object]] = []

    for row in payload:
        row_copy = dict(row)
        existing_match = _current_match(existing_rows, row_copy)

        if existing_match is None:
            inserts.append(
                {
                    "first_name": row_copy.get("first_name"),
                    "last_name": row_copy.get("last_name"),
                    "email": row_copy.get("email"),
                    "phone": row_copy.get("phone"),
                    "comment": row_copy.get("comment"),
                    "loaded_at": now,
                    "effective_start": now,
                    "effective_end": None,
                    "is_current": 1,
                    "version": 1,
                }
            )
            continue

        if _rows_equal(existing_match, row_copy):
            continue

        updates.append((int(existing_match["id"]), now))
        version = int(existing_match["version"] or 1) + 1
        inserts.append(
            {
                "first_name": row_copy.get("first_name"),
                "last_name": row_copy.get("last_name"),
                "email": row_copy.get("email"),
                "phone": row_copy.get("phone"),
                "comment": row_copy.get("comment"),
                "loaded_at": now,
                "effective_start": now,
                "effective_end": None,
                "is_current": 1,
                "version": version,
            }
        )

    with engine.begin() as conn:
        for row_id, effective_end in updates:
            conn.execute(
                text(
                    f"""
                    UPDATE {table_ref}
                    SET effective_end = :effective_end,
                        is_current = 0
                    WHERE id = :id
                    """
                ),
                {"effective_end": effective_end, "id": row_id},
            )

        if inserts:
            conn.execute(table.insert(), inserts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=["local", "sqlserver"],
        default="local",
        help="local = write to a SQLite file for testing (default). "
        "sqlserver = write to AWS SQL Server using AWS_SQL_* env vars.",
    )
    args = parser.parse_args()

    zoom_df = load_zoom_registrations()
    bitrix_df = load_bitrix_contacts()
    merged_df = merge_sources(zoom_df, bitrix_df)

    print(f"Zoom rows:   {len(zoom_df)}")
    print(f"Bitrix rows: {len(bitrix_df)}")
    print(f"Merged rows (after de-dupe): {len(merged_df)}\n")
    print(merged_df.to_string(index=False))

    engine = get_engine(args.target)
    push_to_sql_server(merged_df, engine)

    print(f"\nLoaded {len(merged_df)} rows into '{DEST_TABLE}' on target={args.target}")
    if args.target == "local":
        print(f"Local test DB: {BASE_DIR / 'test_output.db'}")

        readback = pd.read_sql_table(DEST_TABLE, engine)
        print("\nRead-back from destination table:")
        print(readback.to_string(index=False))


if __name__ == "__main__":
    main()
