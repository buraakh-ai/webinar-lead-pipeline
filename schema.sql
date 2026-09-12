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

        -- Source attribution fields for downstream systems such as Zoho
        -- campaign ingestion.
        source_id   NVARCHAR(255)  NULL,
        source_name NVARCHAR(100)  NOT NULL,
        source_type NVARCHAR(100)  NULL,

        first_name  NVARCHAR(100)  NULL,
        last_name   NVARCHAR(100)  NULL,
        email       NVARCHAR(255)  NULL,
        phone       NVARCHAR(30)   NULL,   -- normalized to digits only, no formatting

        -- NVARCHAR(MAX), not a short VARCHAR(n): a Bitrix "Comment" is a
        -- full free-text note (can run to several sentences). A fixed short
        -- length would silently truncate or reject inserts.
        comment     NVARCHAR(MAX)  NULL,

        -- Row insert time, in UTC. Useful for auditing which load a row came from.
        loaded_at   DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME()
    );
END
GO

-- Optional indexes / constraints for downstream analytics.
--
-- CREATE NONCLUSTERED INDEX IX_webinar_leads_source
--     ON dbo.webinar_leads (source_name, source_type, loaded_at);
