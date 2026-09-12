# Webinar Lead Pipeline

Loads webinar/lead data from two separate systems into one destination
table, preserving the source identity for each row so downstream systems
such as Zoho can attribute campaigns correctly.

## Why this exists

Leads currently come in from two places that don't talk to each other:

- **Zoom** webinar registrations (registrant export)
- **Bitrix** CRM (call/lead export)

Each export has far more columns than needed (Bitrix's real export is
~164 columns), so this script first normalizes each source down to the
core lead fields that matter (first name, last name, email, phone,
comment). The output keeps `source_id`, `source_name`, and `source_type`
for every row so Zoho or other downstream apps can attribute the record
back to the original source campaign or feed.

Today the two sources are files (dropped into `sample_data/`); AWS SQL
Server credentials weren't available yet at the time this was built, so
the script defaults to a local SQLite file as a stand-in so the whole
pipeline is runnable and inspectable without any cloud access. The design
anticipates two things changing later without needing a rewrite:

1. **Files -> APIs.** `load_zoom_registrations()` / `load_bitrix_contacts()`
   are the only functions that know about files today; swapping them for
   Zoom API / Bitrix24 REST API calls is a self-contained change --
   everything downstream (normalize, attach source metadata, load) just
   operates on a DataFrame and doesn't care where it came from.
2. **SQLite -> AWS SQL Server.** `push_to_sql_server()` already targets
   real SQL Server via `--target sqlserver` once `AWS_SQL_*` credentials
   are set (see `.env.example`); it just hasn't been run against a live
   instance yet.

## How the source feeds are normalized

Each source export is read independently and normalized into the same
common lead layout:

- `first_name`
- `last_name`
- `email`
- `phone`
- `comment`
- `source_id`
- `source_name`
- `source_type`

The `source_id`, `source_name`, and `source_type` fields are the key
addition for the Zoho campaign use case: they tell downstream apps which
source feed generated each row and which campaign or folder it came from.

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

| column         | type            | notes                                                      |
|----------------|-----------------|-------------------------------------------------------------|
| `id`           | `INT IDENTITY`  | surrogate key                                              |
| `source_id`    | `NVARCHAR(255)` | unique source record identifier, used by Zoho campaign apps |
| `source_name`  | `NVARCHAR(100)` | source system name (`bitrix`, `zoom`)                     |
| `source_type`  | `NVARCHAR(100)` | source feed type (`contact`, `webinar_registration`)       |
| `first_name`   | `NVARCHAR(100)` |                                                             |
| `last_name`    | `NVARCHAR(100)` |                                                             |
| `email`        | `NVARCHAR(255)` |                                                             |
| `phone`        | `NVARCHAR(30)`  | digits only, no formatting                                 |
| `comment`      | `NVARCHAR(MAX)` | full free-text note(s)                                     |
| `loaded_at`    | `DATETIME2`     | audit timestamp                                            |

`merge_and_load.py` creates this table automatically on first run if it
doesn't exist yet (matching `schema.sql`).

This version intentionally does not implement SCD-2 history tracking. Each
source feed can be pushed directly into the destination table with its own
`source_id`, `source_name`, and `source_type`, which makes the table a
clean source for Zoho campaigns and similar downstream consumers.

## Running it

```
pip install -r requirements.txt

# (Re)build the sample data files from the real exports in sample_data/raw/
python scripts/generate_sample_data.py

# Dry run -- loads the sample source feeds into a local SQLite file
# (test_output.db), no AWS access needed. Prints the source rows and a
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
- **No API integration yet** -- still reads local files, not the Zoom/
  Bitrix24 APIs.
- **No scheduling/orchestration** -- nothing runs this automatically yet.
- **No downstream Zoho app wiring yet** -- the destination table is now
  ready to be used as the source for Zoho campaign ingestion, but that
  integration is still future work.

## Layout

```
merge_and_load.py            the pipeline: load -> normalize -> load to SQL
schema.sql                   destination table DDL (SQL Server)
requirements.txt
.env.example                 AWS SQL Server connection env vars (copy to .env)
scripts/generate_sample_data.py   builds sample_data/*.csv from sample_data/raw/
sample_data/raw/             untouched copies of the real Zoom/Bitrix exports shared for this project
sample_data/                 final sample files merge_and_load.py reads (real rows + a few synthetic test rows)
```
