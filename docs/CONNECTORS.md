# Connecting data sources

Data → **Add data**. Every connection has a **Test connection** button; secrets (passwords, keys, tokens)
are stored encrypted and never shown again. Use read-only accounts everywhere.

| Source | Full sync | Incremental | Deletions picked up | Custom query |
| --- | --- | --- | --- | --- |
| CSV / Excel / Parquet upload | Yes | No (re-upload replaces) | n/a | No |
| Oracle | Yes | Yes, by a date/number column | No (schedule a full sync) | SQL |
| PostgreSQL | Yes | Yes, by a date/number column | No (schedule a full sync) | SQL |
| Salesforce | Yes (REST, or Bulk API 2.0 for large objects) | Yes, by `SystemModstamp` | **Yes** (queryAll) | SOQL |
| REST / JSON API | Yes | Yes, if the API has a "changed since" parameter | No | No |

## Try it without real systems

```bash
cd backend
python tools/fake_sources.py        # fake Salesforce on :9100, fake REST API on :9200
ALLOW_LOCAL_API_URLS=true JOBS_MODE=inline uvicorn app.main:app --reload
```

- Salesforce: My Domain URL `http://localhost:9100`, consumer key `lumen-dev`, secret `dev-secret`.
- REST API: base URL `http://localhost:9200`, endpoint `/v1/orders`, records at `data.items`, API key header
  `X-API-Key` = `dev-key`, pages "Page number" with size parameter `per_page`, changed-since `updated_since`.

`ALLOW_LOCAL_API_URLS` is for this local trial only. Never set it on a server.

## Salesforce

### Option A: client credentials (recommended)

1. **Integration user**: create (or pick) a user whose profile/permission sets can *read* the objects and
   fields you want. Salesforce offers a free "Salesforce Integration" user licence for this.
2. **Setup → App Manager → New Connected App** (or **External Client App Manager → New**):
   - Enable OAuth settings. Callback URL: any (e.g. `https://localhost/callback`); it is not used.
   - Selected OAuth scopes: **Manage user data via APIs (api)**.
   - Tick **Enable Client Credentials Flow** and accept the warning.
3. **Manage → Edit Policies**: under *Client Credentials Flow*, set **Run As** to the integration user.
   Permitted users: *Admin approved users are pre-authorized*.
4. **View → Manage Consumer Details**: copy the **Consumer Key** and **Consumer Secret**.
5. In Lumen: My Domain URL (Setup → My Domain, e.g. `https://yourcompany.my.salesforce.com`), consumer key,
   consumer secret → Test connection.

### Option B: JWT bearer (certificate)

1. Create a key pair and certificate:
   `openssl req -x509 -newkey rsa:2048 -nodes -keyout lumen.key -out lumen.crt -days 730 -subj "/CN=lumen-bi"`
2. In the connected app, enable OAuth with scope **api** (and **refresh_token, offline_access**), tick
   **Use digital signatures** and upload `lumen.crt`.
3. Manage → Edit Policies: *Admin approved users are pre-authorized*; add the integration user's profile or
   permission set.
4. In Lumen choose **JWT bearer**, enter the consumer key, the integration username, Production or Sandbox,
   and paste the contents of `lumen.key`.

### Notes

- Username/password sign-in is not offered: Salesforce blocks that OAuth flow by default and is retiring it.
- API version: empty = the newest your org supports (recommended).
- Objects with at least *Use Bulk API from* rows (default 50,000) are read with Bulk API 2.0, which uses far
  fewer API calls. Compound fields (addresses, geolocations) are skipped; their parts (BillingCity…) are kept.
- Incremental datasets default to `SystemModstamp` with key `Id`. Records deleted in Salesforce are removed
  from the copy at the next sync, as long as they are still in the recycle bin (15 days). If a dataset is not
  synced for longer than that, run a full sync.
- Every sync uses API calls from your org's daily allowance; a failed sync because of the limit says so.

## REST / JSON APIs

A connection describes what an API's endpoints share; each dataset then picks one endpoint.

| Setting | Example | Meaning |
| --- | --- | --- |
| Base URL | `https://api.example.com` | |
| Endpoints | `/v1/orders`, `/v1/orders?status=open` | One per line; query strings allowed |
| Records are at | `data.items` | Dotted path to the list in each response; empty if the response *is* the list |
| Sign-in | API key / Bearer / Basic / OAuth 2.0 client credentials | Secrets are encrypted |
| Pages | page number, offset+limit, cursor, next-page URL, Link header | |
| Changed-since parameter | `updated_since`, format ISO / date / Unix | Enables incremental sync |

How data is shaped: nested objects become dotted columns (`customer.name`), lists are kept as JSON text,
and columns whose values are all ISO date-times become real timestamps. Requests are retried on rate
limits (HTTP 429, honouring `Retry-After`) and temporary errors (502/503/504).

For safety, API connections cannot call this server itself (`localhost`) or cloud metadata addresses
(`169.254.x.x`). Company-internal addresses such as `10.x.x.x` are allowed.
