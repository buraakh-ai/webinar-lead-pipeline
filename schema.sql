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
        loaded_at   DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME()
    );
END
GO

-- Optional: uncomment once the loader does an upsert (e.g. T-SQL MERGE)
-- instead of a plain INSERT. Without an upsert, a unique constraint here
-- would make merge_and_load.py fail on its second run against the same
-- data, since every run currently just inserts. Filtered (WHERE email IS
-- NOT NULL) because a plain unique index only allows one NULL row, but
-- leads with no email at all (Bitrix rows with just a phone) are valid and
-- there can be more than one.
--
-- CREATE UNIQUE INDEX UX_webinar_leads_email
--     ON dbo.webinar_leads (email)
--     WHERE email IS NOT NULL;
