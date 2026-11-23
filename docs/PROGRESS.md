# Progress

## Status

| # | Milestone | Status |
| --- | --- | --- |
| 1 | Foundation (repo, Docker, CI, migrations) | Done |
| 2 | Auth, users, roles, groups, audit | Done (TOTP two-factor deferred to milestone 12) |
| 3 | SSO: Microsoft Entra ID, UAE PASS | Done (real Microsoft/UAE PASS sign-in still to be tried by the owner) |
| 4 | Connector framework + files + Oracle (+ PostgreSQL) | Done (Oracle still to be tried against a real database) |
| 5 | Salesforce and REST connectors | Done (to be tried against a real Salesforce org) |
| 6 | Semantic layer | Done |
| 7 | Query engine + row-level security | Done |
| 8 | Chart builder UI | Next |
| 9 | Dashboards UI | — |
| 10 | Exports, subscriptions, alerts, embedding | — |
| 11 | AI features | — |
| 12 | Admin and hardening | — |

## What exists (milestones 1–2)

- Docker Compose: postgres, redis, api (runs migrations + creates first admin on start), worker, beat, web
  (Caddy serving the built frontend and proxying `/api`).
- Auth: email/password login, access + refresh JWTs in httpOnly cookies, refresh rotation, logout and
  "sign out everywhere" (via `users.token_version`), lockout after `MAX_FAILED_LOGINS`, password policy,
  forgot/reset password by email (logs the email when SMTP is not set), change password, Bearer tokens for
  API clients, CSRF header check.
- Users (admin): list/search/filter, create (optional invite email), edit role/attributes/groups/password,
  activate/deactivate, unlock, send reset, sign out everywhere, delete, CSV import. Last-admin protection.
- Groups (admin): CRUD, members. Audit log (admin) with filters.
- Frontend: sign-in, forgot/reset password, layout with role-aware navigation, home, account page,
  Users / Groups / Audit admin pages.
- Tests: 27 backend tests (SQLite and PostgreSQL); browser smoke test of the admin flow done manually.

## Decisions

- Sync SQLAlchemy (FastAPI runs it in a thread pool): simpler and fast enough at this scale.
- Emails validated leniently so internal domains like `name@company.local` work.
- UI components are hand-written in `frontend/src/components/ui.tsx` (Tailwind), not the shadcn CLI.
- Roles are ordered: viewer < explorer < creator < admin; `require_role(min)` checks the level.
- AI provider (owner, 2026-11-22): **both** cloud API (Claude/OpenAI) and self-hosted (OpenAI-compatible,
  e.g. Ollama/vLLM). Datasets marked sensitive use only the self-hosted provider. See SPEC feature 7.

## What exists (milestone 3)

- `identity_providers` (secrets encrypted with `ENCRYPTION_KEY`, see `app/core/crypto.py`) and
  `user_identities` (one user can link several providers). Migration `0002`.
- `app/services/sso.py`: adapters for Microsoft Entra ID (single tenant; ID token verified against JWKS,
  issuer, audience, nonce; PKCE), UAE PASS (authorization code; Basic auth with query parameters at the token
  endpoint; profile from userinfo; minimum SOP level; no PKCE/ID token, as documented), generic OIDC
  (discovery URL). Account resolution: by linked identity, then email (if allowed), then auto-create
  (if allowed), with allowed-domain checks; attribute and group mapping applied at every sign-in.
- `app/api/v1/sso.py`: `/auth/providers`, `/auth/sso/{id}/start`, `/auth/sso/{id}/callback` (state in a
  signed short-lived cookie; open-redirect protection), admin CRUD `/admin/identity-providers`.
- Logout returns the provider's logout URL (UAE PASS requires signing out there too); the frontend follows it.
- Frontend: SSO buttons and errors on the login page; Admin → Sign-in methods; linked methods on Account and
  in the user editor.
- `backend/tools/dev_idp.py`: local test OIDC provider. Setup guide: `docs/SSO_SETUP.md`.
- Tests: 55 backend tests, including a fake IdP (forged signatures, wrong audience/issuer/nonce, expired
  tokens, state mismatch, missing state cookie, open redirect). Browser test of the full flow with dev_idp.

## What exists (milestone 4)

- Connector interface `app/connectors/base.py` (`test_connection`, `list_objects`, `get_schema`,
  `extract` → Arrow record batches, always at least one batch so empty tables still have columns).
- `app/connectors/sql.py`: shared database logic: identifier whitelist + quoting (no escaping tricks),
  custom SQL limited to one SELECT/WITH, incremental `WHERE col > :since` (`>=` when a primary key exists).
- Oracle (`oracle.py`, python-oracledb thin mode; service name or SID; schemas filter; readable ORA errors),
  PostgreSQL (`postgres.py`), files (`files.py`: CSV/TSV via DuckDB, Excel every sheet via openpyxl,
  Parquet). Excel columns mixing numbers and text are kept as text.
- `connections` (secrets encrypted JSON), `datasets`, `sync_runs` tables. Migration `0003`.
- Snapshots `app/services/snapshots.py`: `DATA_DIR/datasets/<id>/v<n>/data.parquet`, written to a temp
  folder then renamed (atomic), incremental merge by primary key with DuckDB (ANTI JOIN + UNION BY NAME),
  last `SNAPSHOTS_TO_KEEP` versions kept, schema changes recorded on the run.
- Sync engine `app/services/sync.py`: one active run per dataset, failure keeps the previous version live
  and emails the owner; `JOBS_MODE` celery / inline / eager. Celery Beat runs `dispatch_due_syncs` every
  minute; each dataset has a cron schedule in `TIMEZONE` (minimum every 5 minutes).
- API `app/api/v1/data.py` (Creators and Admins): connection types, connections CRUD + test (also unsaved),
  objects, schema, file upload/replace (re-syncs dependent datasets), datasets CRUD, sync now, runs,
  preview. Creators can see and reuse every connection/dataset; only the owner or an admin can change one.
  Changing what a dataset extracts rebuilds its snapshot from scratch.
- Frontend: Data home, connection page (test, edit/replace file, tables list → create dataset), dataset
  form (table or SQL, full/incremental, key, schedule presets), dataset page (status, preview, columns,
  sync history with schema changes), live polling while syncing.
- Tests: 92 backend tests. Real PostgreSQL source end to end (runs when `TEST_DATABASE_URL` is PostgreSQL,
  as in CI); Oracle connector code exercised with SQLite standing in for the driver. Browser test: CSV
  upload → dataset → preview; PostgreSQL connection → incremental dataset with schedule → new source rows →
  Sync now → incremental merge.

Known limits (by design for now):
- Incremental syncs do not see rows deleted at the source; schedule an occasional full sync if that matters.
- Oracle "live mode" (query the database directly instead of a snapshot) comes with the query engine (M7).
- Data preview is limited to Creators/Admins until row-level security exists (M7).
- Fixed: `.gitignore` had `data/`, which also ignored `frontend/src/pages/data/`; now anchored to `/data/`.

## What exists (milestone 5)

- `app/connectors/http.py`: shared HTTP for API connectors: retries on 429/502/503/504 with `Retry-After`
  and back-off, safe manual redirects, and a guard that blocks localhost, link-local and metadata addresses
  (company-internal ranges allowed). `ALLOW_LOCAL_API_URLS=true` only for local trials.
- `app/connectors/salesforce.py`: client credentials and JWT bearer sign-in (username-password not offered:
  blocked/retired by Salesforce); newest API version auto-detected; queryable objects with labels; describe
  (compound/binary fields skipped) with typed columns; REST query with `nextRecordsUrl` paging; Bulk API 2.0
  (job, polling, CSV results with `Sforce-Locator`) when a COUNT() reaches `bulk_threshold`; incremental by
  `SystemModstamp` using queryAll so deleted records are removed; custom SOQL with relationship fields
  flattened (`Account.Name`); re-sign-in on expired sessions; readable API-limit and query errors.
- `app/connectors/rest.py`: base URL + endpoints per connection; API key (header/query), bearer, basic,
  OAuth 2.0 client credentials (token refresh on 401); pagination page / offset / cursor / next link / Link
  header with a max-pages guard; records path; nested JSON flattened, lists kept as JSON, ISO date-time
  strings typed; incremental through a "changed since" parameter (ISO, date, epoch, epoch ms).
- Snapshot writer: rows flagged with `_lumen_deleted` by a connector are dropped and remove their older
  versions (generic, used by Salesforce).
- Previews show times in `TIMEZONE`.
- `tools/fake_sources.py`: fake Salesforce (OAuth, describe, SOQL subset, query/queryAll paging, Bulk API 2.0)
  and fake REST API (5 pagination styles, 4 auth types, rate limiting). Same code serves tests
  (MockTransport) and local trials (real HTTP servers on :9100/:9200).
- Frontend: Salesforce and REST connection forms; object labels; SOQL wording; Salesforce defaults
  (SystemModstamp + Id); incremental disabled with a reason when a REST connection has no since parameter.
- Docs: `docs/CONNECTORS.md` (Salesforce connected-app steps for both sign-in methods, REST settings).
- Tests: 124 backend tests (32 new). Browser test against the fakes running as real servers.

## What exists (milestone 6)

- Formula language `app/semantic/formula.py`: tokenizer → parser (AST) → type-checking compiler to DuckDB SQL.
  `[Field]`, `@param`/`@[param name]`, `#date#`, operators, IF/ELSEIF/CASE, IN, IS NULL, ~60 whitelisted
  functions (aggregates, logic, number, text, date, conversion). Tracks row vs aggregate level (mixing is an
  error), detects cycles, reports errors with character positions. Literals escaped, identifiers quoted,
  division by zero → NULL. Never pastes user text into SQL.
- Models `dataset_fields` (column/calculated/group/bin; label, description, role, type, format, default
  aggregation, hidden, formula, config, is_aggregate, error, missing, position), `hierarchies`, `parameters`;
  `datasets.kind` (source/join/union) and nullable `connection_id`. Migration `0004` (server default for kind).
- `app/semantic/model.py`: defaults for new columns (prettified labels, role heuristics, formats),
  `reconcile_fields` after every successful sync (edits kept, new columns added, vanished ones marked
  missing), `build_context` (fields + parameter values → compiler context, optional table alias for joins
  in M7), `recheck_calculated` (type/aggregate/error per field), dependency helpers.
- `app/semantic/derived.py`: joined (left/inner/full, multi-column keys, chained joins with `alias.column`
  names) and unioned (by name, optional source column) datasets, materialised from parents' snapshots with
  DuckDB; validated (columns, cycles); rebuilt automatically when a parent syncs (`trigger = upstream`);
  parents cannot be deleted while used.
- API `app/api/v1/model.py`: fields list/create/update/reorder/delete, formula check with sample values,
  distinct values of a field, hierarchies CRUD, parameters CRUD (typed defaults, allowed values, usage
  checks). Renaming a calculated field rewrites references. Preview includes row-level calculated fields.
- Frontend: dataset **Model** tab (field table with inline label/role/format/aggregation/hidden; calculated
  field editor with live check, error highlight, field/parameter picker and function reference; group and
  bin editors; hierarchies; parameters) and **Join or combine datasets** builder with key guessing.
- Docs: `docs/MODELLING.md` (fields, formula reference, groups/bins, hierarchies, parameters, joins).
- Tests: 204 backend tests (62 formula unit tests incl. injection attempts, 18 model/join API tests).
  Browser test: model edits, formula error and fix, aggregate field, group, parameter, hierarchy, preview of
  calculated columns, join builder → joined dataset.

## What exists (milestone 7)

- Query engine `app/query/`: `spec.py` (the query spec, Pydantic), `engine.py` (compiler and runner),
  `rls.py` (row-level security), `cache.py` (Redis or in-process result cache), `duck.py` (one shared
  DuckDB instance per process, a cursor per query, timeout by interrupt, TimeZone per query).
  - Dimensions with date grain/part, measures with aggregations (incl. median, percentile, stdev) or
    aggregate calculated fields, `*` row count; filters (comparison, in/not in with nulls, range, text,
    null, relative dates, top/bottom N ranked after other filters, aggregate filters as HAVING); sort,
    limit with `truncated`, grand totals, parameters; rows mode.
  - Table calculations over the aggregated result: running total, % of total, difference, % change, rank,
    moving average, with `along` / `partition_by`.
  - SQL shape: `base` CTE (the only read of the snapshot, carrying RLS) → `q` (group by) → `calc` (window
    functions) → order/limit. User values are `$n` bound parameters.
  - Cache key = sha256(SQL + bound values + snapshot file identity): security context and data version
    are part of the key by construction. `CACHE_BACKEND=redis|memory|off`, `CACHE_SECONDS`.
  - 10M rows on a 2-vCPU sandbox, uncached: 45–390 ms per chart query (target < 500 ms on 8 vCPU).
- RLS models `rls_rules` (attribute | entitlement; exempt groups; enabled) and `dataset_shares`
  (everyone | user | group). Migration `0005`. Fail closed in every unclear case; admins bypass; Creators
  are filtered too; derived datasets inherit parents' rules through column mappings (transitively).
  Built-in attributes email/user_id/name/groups cannot be overridden by custom attributes. Deleted users
  and groups are removed from shares and exemptions. Fields used by rules cannot be deleted; renames
  update rules; access tables in use cannot be deleted.
- API `app/api/v1/query.py`: `POST /query`, `POST /query/export` (CSV, Explorer+, formula-injection safe,
  audited), `GET /catalog/datasets[/{id}]` (formulas hidden from non-creators), security
  (`GET /datasets/{id}/security`, rules CRUD, `PUT /access`, `GET /security/check?user_id=`), `as_user_id`
  preview for owners/admins (audited), `GET /directory` (people and groups for Creators). The existing
  preview, value list and formula sample endpoints now go through RLS; value lists are open to anyone
  with access to the dataset.
- Frontend: dataset **Security** tab (who can use it; rules list incl. inherited ones; rule editor for
  attribute and access-table rules with exempt groups; check what someone sees with rule status, values,
  row count and their rows); preview shows when RLS applies.
- Docs: `docs/SECURITY.md`, `docs/QUERY_API.md`.
- Tests: 287 backend tests; `test_query.py` (44: every filter op, table calcs, totals, sort/limit, cache
  refresh on sync, export, catalog) and `test_rls.py` (39: every read path, fail-closed cases, joins and
  unions, cache isolation, permissions, injection attempts). Browser test: access, rule with exempt group,
  check as three users, queries as five users with different access, restricted preview for a creator.
- Not done (optional in the spec): Oracle live mode. Snapshots + DuckDB meet the performance targets, so
  live mode is left for later if a real need appears.

## Next: milestone 8 (chart builder)

- Chart page: pick a dataset from the catalog, field list (dimensions/measures, hierarchies, parameters),
  shelves (Columns, Rows, Color, Size, Label, Tooltip, Detail, Filters) that build a query spec.
- Show Me: bar, line, area, combo, pie/donut, scatter, table / pivot with totals, KPI card, heat map,
  treemap, map (UAE emirates/GCC/world); quick table calcs; top N; reference and trend lines; conditional
  formatting; formats from the model.
- Filter cards (values from `/values`, ranges, relative dates), parameter controls, drill through
  hierarchies, view underlying rows (rows mode), export CSV.
- Saved charts (`charts` table: owner, dataset, spec, visual settings), Arabic/RTL-ready layout.

## Open questions for the owner

- Self-hosted AI: is there (or will there be) a GPU server to run the local model?
- Hosting: on-premise or UAE cloud region?
- Arabic interface from day one?
- First Salesforce objects and Oracle schemas?
