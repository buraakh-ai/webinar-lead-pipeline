-- Destination table for merge_and_load.py, for AWS SQL Server (T-SQL).
--
-- You don't have to run this by hand: push_to_sql_server() in
-- merge_and_load.py creates this same table automatically (via SQLAlchemy's
-- metadata.create_all()) the first time you run with --target sqlserver, if
-- it doesn't already exist. This file exists so a DBA can review/run the
-- exact DDL ahead of time, or so you can create the table with the optional
-- index below (the Python auto-create path does not add it). Keep the two
-- in sync if you change one.

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'webinar_leads')
BEGIN
    CREATE TABLE dbo.webinar_leads (
        id          INT IDENTITY(1,1) NOT NULL PRIMARY KEY,

        first_name  NVARCHAR(100)  NULL,
        last_name   NVARCHAR(100)  NULL,
        email       NVARCHAR(255)  NULL,
        phone       NVARCHAR(30)   NULL,   -- normalized to digits only, no formatting

        -- NVARCHAR(MAX), not a short VARCHAR(n): a Bitrix "Comment" is a
        -- full free-text note (can run to several sentences), and merged
        -- leads concatenate multiple sources' comments with " | " -- a
        -- fixed short length will silently truncate or reject inserts.
        comment     NVARCHAR(MAX)  NULL,

        -- Row insert time, in UTC. Not one of the 5 business columns --
        -- kept for auditing/troubleshooting which load a row came from.
        loaded_at       DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME(),

        -- SCD-2 history columns: every time the lead changes, the prior
        -- current row is closed and a new row with a higher version is
        -- inserted. This lets the pipeline stay idempotent for unchanged
        -- input, while preserving historical versions for changed records.
        effective_start DATETIME2(0)   NULL,
        effective_end   DATETIME2(0)   NULL,
        is_current      INT            NULL,
        version         INT            NULL
    );
END
GO

-- Optional indexes / constraints for downstream analytics.
-- The Python loader currently performs SCD-2 versioning via MERGE/update
-- statements, so it doesn't require a unique constraint to make reruns safe.
-- These are included here for convenience if you want to query the current
-- record more efficiently.
--
-- CREATE NONCLUSTERED INDEX IX_webinar_leads_current
--     ON dbo.webinar_leads (is_current, email)
--     WHERE is_current = 1;
