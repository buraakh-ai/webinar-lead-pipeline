# Team Requirements Checklist

Use this file as the handoff checklist for the remaining items needed to finish the app end-to-end.

## Bitrix API
- Bitrix24 domain URL
- OAuth app credentials (`client_id`, `client_secret`, `redirect_uri`)
- required Bitrix scopes / permissions
- which objects to pull (`contacts`, `leads`, `deals`, custom fields if needed)
- whether to use an OAuth token flow or an existing token / refresh token
- a test Bitrix workspace or sandbox, if available
- any filters needed for the sync (date range, status, stage, etc.)

## Zoom API
- Zoom account / workspace details
- OAuth credentials (`client_id`, `client_secret`, `account_id`)
- webinar / registrant access permissions
- which webinars or meeting exports should be pulled
- whether the app should process all registrants or only selected webinars
- a test webinar or sample live data to validate responses

## RDS / SQL Server
- RDS endpoint / server hostname
- port
- database name
- username and password
- schema name if not using the default
- ODBC driver version needed
- any firewall / VPN / private network access details
- whether the app should create the table automatically or use an existing table

## Zoho / downstream destination
- Zoho campaign or destination details
- API credentials if Zoho API is part of the flow
- mapping rules for `source_id`, `source_name`, and `source_type`
- which fields should be pushed to Zoho
- any required lead lifecycle / campaign status fields

## Operational requirements
- expected sync frequency (`hourly`, `daily`, `on-demand`, etc.)
- error handling approach (`retries`, `alerts`, `logging`)
- whether duplicate leads should be merged or kept as separate rows
- validation checks expected after each run

## Test data needed
- one sample live Bitrix export
- one sample live Zoom export
- expected row counts for those samples
- at least one duplicate or edge-case example for testing

## Secrets / environment setup
- where secrets should live (`.env`, AWS Secrets Manager, Azure Key Vault, etc.)
- who owns credential rotation
- whether local dev and production should use separate settings

## Minimum set to unblock the project
If you want the shortest possible list, these are the must-haves:
- Bitrix OAuth credentials
- Zoom OAuth credentials
- RDS / SQL Server hostname, database name, username, and password
- Zoho destination details or mapping requirements
- expected sync schedule
- one sample live export from Bitrix and one from Zoom
