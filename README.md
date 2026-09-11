# Webinar Lead Pipeline

Merges webinar/lead data from two separate systems into one destination
table, so sales has a single, de-duplicated list of leads instead of two
disconnected exports.

## Why this exists

Leads currently come in from two places that don't talk to each other:

- **Zoom** webinar registrations (registrant export)
- **Bitrix** CRM (call/lead export)

Each export has far more columns than needed (Bitrix's real export is
~164 columns), and the same person can show up in both -- or more than
once within Bitrix alone (e.g. a call logged twice a minute apart). This
script pulls just the 5 fields that matter (first name, last name, email,
phone, comment), merges the two sources, collapses duplicates, and loads
the result into a destination table that now supports SCD-2-style history
tracking.

Today the two sources are files (dropped into `sample_data/`); AWS SQL
Server credentials weren't available yet at the time this was built, so
the script defaults to a local SQLite file as a stand-in so the whole
pipeline is runnable and inspectable without any cloud access. The design
anticipates two things changing later without needing a rewrite:

1. **Files -> APIs.** `load_zoom_registrations()` / `load_bitrix_contacts()`
   are the only functions that know about files today; swapping them for
   Zoom API / Bitrix24 REST API calls is a self-contained change --
   everything downstream (normalize, merge, dedupe, load) just operates on
   a DataFrame and doesn't care where it came from.
2. **SQLite -> AWS SQL Server.** `push_to_sql_server()` already targets
   real SQL Server via `--target sqlserver` once `AWS_SQL_*` credentials
   are set (see `.env.example`); it just hasn't been run against a live
   instance yet.

## How leads get merged

A lead from one source is treated as the same lead as one from the other
if, in order:

1. they share the same **email**, or
2. they share the same **phone number**, or
3. neither of the above matched, but their **names are a close fuzzy
   match** (handles the same person being logged with a work
   email/phone in one system and a personal one in the other, and/or
   their name typed slightly differently by whoever entered it)

Each merged row keeps a `match_reason` (`email`, `phone`, `email+phone`,
`fuzzy_name`, or `unique`) during processing so it's auditable *why* two
records were merged -- this is dropped before the row is written to the
destination table, since it's not one of the 5 business columns.

Column matching against the source files (`_resolve_columns()` in
`merge_and_load.py`) is exact-match-first, not loose substring matching --
important because the real Bitrix export has several look-alike columns
(`Comment` vs. `Instagram comments`; plain `First Name` vs. `Shareholder 1
First Name`; `Work E-mail` vs. `Home E-mail`/`Other E-mail`). It also
tolerates a few metadata/title rows above the real header row, by scanning
the first several rows of the file to find the one that actually looks
like a header.

## Destination schema

See [schema.sql](schema.sql) for the full DDL and reasoning (e.g. why
`comment` is `NVARCHAR(MAX)` and not a short `VARCHAR`). Summary:

| column           | type            | notes                                                    |
|------------------|-----------------|-----------------------------------------------------------|
| `id`             | `INT IDENTITY`  | surrogate key, not one of the 5 fields                   |
| `first_name`     | `NVARCHAR(100)` |                                                           |
| `last_name`      | `NVARCHAR(100)` |                                                           |
| `email`          | `NVARCHAR(255)` |                                                           |
| `phone`          | `NVARCHAR(30)`  | digits only, no formatting                               |
| `comment`        | `NVARCHAR(MAX)` | full free-text note(s)                                   |
| `loaded_at`      | `DATETIME2`     | audit timestamp, not one of the 5                        |
| `effective_start`| `DATETIME2`     | when this version became current                        |
| `effective_end`  | `DATETIME2`     | when this version stopped being current                  |
| `is_current`     | `INT`           | `1` for the active version, `0` for historical versions  |
| `version`        | `INT`           | monotonically increasing version number for a lead       |

`merge_and_load.py` creates this table automatically on first run if it
doesn't exist yet (matching `schema.sql`).

In the current first phase, the loader keeps the destination idempotent for
unchanged input and creates a new version when the lead's data changes.
This is an SCD-2-style history model, but it is intentionally lightweight
and meant to evolve as the project grows.

## Running it

```
pip install -r requirements.txt

# (Re)build the sample data files from the real exports in sample_data/raw/
python scripts/generate_sample_data.py

# Dry run -- merges the sample files and loads into a local SQLite file
# (test_output.db), no AWS access needed. Prints the merged data and a
# read-back from the destination table.
python merge_and_load.py

# Real run, once AWS SQL Server credentials are set (copy .env.example -> .env
# and fill in AWS_SQL_SERVER / AWS_SQL_DATABASE / AWS_SQL_USERNAME / AWS_SQL_PASSWORD):
python merge_and_load.py --target sqlserver
```

## What's not done yet

- **Not tested against real AWS SQL Server** -- no credentials were
  available yet when this was built. `--target sqlserver` is wired up but
  unverified against a live instance (network access, ODBC driver, auth).
- **This is only the first phase of SCD-2** -- the current loader keeps
  history by versioning rows, but it does not yet add a full business-key
  model, row-level provenance beyond `loaded_at`, or a more formal
  historical query layer.
- **No API integration yet** -- still reads local files, not the Zoom/
  Bitrix24 APIs.
- **No scheduling/orchestration** -- nothing runs this automatically yet.

## Layout

```
merge_and_load.py            the pipeline: load -> merge/dedupe -> load to SQL
schema.sql                   destination table DDL (SQL Server)
requirements.txt
.env.example                 AWS SQL Server connection env vars (copy to .env)
scripts/generate_sample_data.py   builds sample_data/*.csv from sample_data/raw/
sample_data/raw/             untouched copies of the real Zoom/Bitrix exports shared for this project
sample_data/                 final sample files merge_and_load.py reads (real rows + a few synthetic test rows)
```
