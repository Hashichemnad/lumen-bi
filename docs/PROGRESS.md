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
| 8 | Chart builder UI | Done |
| 9 | Dashboards UI | Done |
| 10 | More chart types, dashboard sections, exports | Done |
| 11 | UI/UX redesign, themes, Arabic/RTL, workspaces, calendars, kiosk mode | Done (layout changes from the owner's testing to follow) |
| 12 | Subscriptions, alerts, embedding, masking, KPI goals, data quality, versions | Done (Teams and WhatsApp to be tried with real accounts) |
| 13 | AI and MCP server | Next |
| 14 | Admin and hardening | — |

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

## What exists (milestone 8)

- Backend: `charts` table (name, description, dataset, owner, `config` = builder state, `query` = the
  query spec it runs, so the server can rerun it for dashboards, reports and alerts). Migration `0006`.
  API `app/api/v1/charts.py`: list (own; admins all), create (Explorer+, dataset must be usable, query
  validated against the spec), get/update/delete (owner or admin), copy. Audited. A dataset with charts
  cannot be deleted. Tests `test_charts.py`.
- Frontend chart library `src/lib/chart/`: `types.ts` (pills, filters, config), `query.ts` (config →
  query spec; mark filters; underlying rows), `showme.ts` (chart types, requirements, automatic choice),
  `build.ts` (result → ECharts option for bars, stacked, lines, areas, bars + line, scatter, heat map,
  treemap, donut, filled and bubble maps), `palette.ts` (validated categorical palette in fixed order,
  folding into "Other"; sequential ramp), `format.ts` (field formats, date grains, compact numbers),
  `geo.ts` (country names/codes in English and Arabic, emirates and Gulf cities, Natural Earth 1:50m
  loaded on demand, zoom to the data).
- `src/components/chart/`: `EChart.tsx` (tree-shaken ECharts, canvas, resize, mark clicks, PNG),
  `ChartView.tsx` (also flat table with sorting, totals and shading; pivot table; number tiles with
  sparkline). Reusable by dashboards.
- Builder `src/pages/charts/`: field panel (dimensions, hierarchies, measures, number of rows,
  parameters), drag and drop shelves (Columns, Rows, Color, Size, Label, Tooltip, Detail, Filters), pill
  menus (totals, quick calculations, date levels and parts, drill, sort, dimension ↔ measure), filter
  editor (values with search and counts, range incl. aggregated, relative dates, top/bottom N, text),
  Show Me, options (labels, legend, totals, shading, 100% stack, smooth, trend line, reference line, row
  limit), mark menu (keep only, exclude, drill down, view data), view data with CSV export, CSV and PNG
  export, undo/redo, swap, save / save copy, unsaved warning. Charts list with copy and delete. The
  builder and ECharts are loaded only when opened; the world map only when a map is drawn.
- Chart design rules follow the data-visualisation guidance (validated palette, one axis, colour follows
  the value, thin marks, legends only for 2+ series): `docs/CHARTS.md`.
- Tests: 294 backend tests. Browser test: builds bars (with colour), line with running total and a values
  filter, country map, drill down from a bar into emirates, view data, emirate bubble map, pivot table
  with shading, heat map, number tiles with sparklines, scatter with trend line, donut, treemap, sorted
  horizontal bars with an average line; saves, reloads and lists the chart; no page errors.

## What exists (milestone 9)

- Backend: `dashboards` (config: tabs of tiles with desktop and optional phone positions on a 12/4-column
  grid; filters with targets per dataset; options), `dashboard_shares` (everyone/user/group, view/edit),
  `dashboard_favorites`. Migration `0007`. `app/services/dashboards.py`: access levels (manage/edit/view;
  viewers never edit), strict config validation (ids, grid bounds, https-only images and web pages, tile
  and filter limits, newly added charts must be the editor's own), and `chart_spec`, which runs a tile's
  saved chart query with only narrowing additions: filters, parameters, drill along a date or a hierarchy
  of the same dataset, rows ("view data", can be switched off).
- API `app/api/v1/dashboards.py`: list (visible to the user, with level/favourite), create (Explorer+),
  get (with the charts' configs and the datasets' fields, hierarchies and parameters), update (edit
  level), copy, delete (manage), favourite on/off, sharing get/set (manage, audited), `POST /query`
  (one tile, viewer's RLS), `POST /export` (CSV, Explorer+, audited), `GET /values` (filter values from a
  dataset used on the dashboard, RLS). Charts on dashboards cannot be deleted. `/directory` opened to
  Explorers (sharing). Deleted users/groups are removed from dashboard shares.
- Frontend: dashboards list (all / favourites / mine / shared, search), home page shows favourites and
  latest; dashboard page with react-grid-layout (drag/resize in edit mode, auto-save with status), tiles
  (charts via the dashboard endpoint, text with a safe Markdown subset, images, sandboxed web pages),
  tabs, filter bar (multi-select with search, single value, text search, date presets and custom range,
  numeric range, parameters, reset), cross-filtering by clicking a mark (applies to other datasets with the
  same field names), drill down/up per tile, view data and CSV/PNG per tile, links that keep filters,
  parameters and tab (`?s=`), phone layout (custom or stacked), sharing dialog, favourites, copy.
- Docs: `docs/DASHBOARDS.md`.
- Tests: 313 backend tests (19 dashboard tests: sharing levels, viewers query through the dashboard with
  their own RLS and without dataset access, filters narrow only, drill limits, view-data switch, values
  and export permissions, layout validation incl. unsafe URLs, editors adding only their own charts,
  groups cleanup, favourites/copy/delete). Browser test: builds a dashboard with 5 charts from 2 datasets
  and a note, 3 filters (incl. one linked across datasets), uses filters, cross-filters, drills, views
  data, reopens a copied link with the filter kept, shares; a UAE-only viewer sees UAE rows; phone view.

## Plan agreed with the owner (23 Nov 2026)

See SPEC milestones 10–14. Owner decisions: versions keep only the last 2 (milestone 12); data entry
(write-back) optional, decided later (milestone 14); MCP = Lumen as an MCP server for Claude (milestone 13);
the UI must be attractive and very easy to use, with designs shown before building (milestone 11).

## What exists (milestone 10)

- 21 more chart types (`frontend/src/lib/chart/build2.ts`): pyramid, stacked pyramid, parallel
  coordinates, radar, timeline / time bars / time combo (time axis + zoom slider), calendar heat map, Gantt
  (custom series), gauge, ring (polar), progress bar, bullet, rating (React stars), dots across / up,
  waterfall, stacked waterfall, funnel (labels beside stages), Origami (custom series: blocks sized by value
  joined by folds), Sankey, Geo map (uploaded boundaries). Show Me grouped (Tables, Bars, Lines, Dates &
  times, Gauges, Circles & dots, Grids, Maps, Pipelines) with per-type requirements; new options (gauge
  maximum, rating full marks, totals, order, map).
- Map boundaries: `map_boundaries` (migration `0008`), files in `DATA_DIR/boundaries/`, API
  `app/api/v1/boundaries.py` (list/read for everyone, upload Creator+, rename / name property / delete by
  uploader or admin, refused while a chart uses it; GeoJSON validated, points dropped, 25 MB). Data → Maps
  page with preview.
- Dashboard sections: tile kind `section` (ID, name, description); navigator on the right with
  scroll-spy, phone picker, links to `#section-<id>`.
- Exports: dashboards to PDF (A4 landscape, page per section, page numbers, filters listed) and PNG via
  headless Chromium (`app/services/render.py`, Celery task `lumen.render_dashboard`, inline in
  development); the browser opens a print view as the user (5-minute session cookie, their RLS) and waits
  for `window.__lumenReady`. Caddy has an internal listener `:8081` for the renderer
  (`RENDER_BASE_URL`); Chromium is installed in the backend image. Excel export (`app/services/excel.py`,
  formats kept, formula-safe) for queries and dashboard tiles; CSV/Excel buttons in the builder, view data
  and tile menus.
- Tests: 319 backend tests (sections, boundaries, Excel formats, render endpoint permissions and errors).
  Browser test: uploads a map, builds a 5-section KPI dashboard with all 21 new types, checks the
  navigator (click, scroll-spy, link to a section, phone picker), exports PDF (7 pages) and PNG.

## What exists (milestone 11)

- Designs first: a design canvas with home, the KPI dashboard and the chart builder in the three themes, plus
  the dashboard in Arabic. The owner chose **Classic** as the default; layout changes will follow their testing.
- Themes (`frontend/src/index.css`): every screen uses the slate / brand / status scales, and each theme
  redefines them as CSS variables on `<html data-theme>`: **Classic** (light, roomy), **Advanced** (dark,
  Space Grotesk headings, larger numbers), **Analyst** (Tableau-style: IBM Plex Sans, square corners, denser
  14px base, solid blue/green field pills; no Tableau names or assets). Fonts are bundled (`@fontsource`), so
  nothing is loaded from the internet. Charts follow the theme (`applyChartTheme` in `lib/chart/palette.ts`):
  the dark theme uses the documented dark steps of the same eight hues, validated on the dark surface #141922
  (worst adjacent CVD ΔE 8.4, normal-vision 19.3, all ≥ 3:1), and dark ink, grid, tooltip and map colours.
- Preferences and settings: `users.preferences` (theme, language, calendar) and `app_settings` (key
  `regional`: default theme and language, calendar display, week start, weekend, fiscal year start). API
  `GET /settings` (everyone: organisation settings + their effective look), `PUT /admin/settings/regional`
  (admins, audited), `PUT /me/preferences`. Migration `0009`. Screens: My account → Appearance (theme cards,
  language, calendar; "use the organisation's default"), Admin → Settings. The look is applied before the
  first paint from the last one used in the browser, then from the server.
- Arabic and right to left: `t()` (`lib/i18n`) looks texts up by their English wording; `ar.ts` holds about
  1,070 Arabic translations covering every screen; `dir="rtl"`, logical CSS (start/end instead of left/right),
  mirrored icons that point, IBM Plex Sans Arabic, Western digits (as used in the UAE), Arabic month and day
  names in charts. The dashboard grid is mirrored in Arabic. Fixed error messages from the server are
  translated too. The sign-in page has a language switch.
- Calendar: Hijri dates (Umm al-Qura) shown instead of or beside Gregorian dates (lists, filters, day labels
  in charts, the home page). Query engine: new date levels `fiscal_year` / `fiscal_quarter` (grain and part),
  relative filters for fiscal quarters and years, and weeks that start on the organisation's first day of the
  week. Fiscal years are named by the year they end in (July 2026 – June 2027 = FY2027). Calendar charts
  start the week on that day and shade the weekend. New date presets: this week, this fiscal quarter, this
  fiscal year, last 2 fiscal years.
- Workspaces: `workspaces` and `workspace_members` (users or groups as viewer / editor / manager), and
  `workspace_id` on dashboards and charts (empty = personal). Admins create and delete workspaces (items go back
  to their owners); managers rename them and manage members (cannot remove their own manager role). Roles
  give dashboard and chart levels view / edit / manage, combined with sharing (the best wins); people with
  the Viewer role are always viewers. Membership never gives dataset access: charts still run with each
  person's row-level security. API `app/api/v1/workspaces.py`, `PUT /dashboards/{id}/workspace`,
  `PUT /charts/{id}/workspace`, `workspace_id` on create and as a list filter (0 = personal). Audited.
  Screens: workspace picker in the side menu (filters home, dashboards and charts; new items go into the
  picked workspace), Workspaces page with members, "Move to a workspace" on dashboards and charts.
- Redesign: new side menu (workspace picker, admin section, account card; phone drawer), home (search across
  dashboards, charts and datasets with Ctrl K, greeting with the date, quick actions, favourite dashboards with
  a small picture of their layout, recent items, data health, workspaces), dashboard header (breadcrumb, data
  freshness, Present, TV mode, Export, Share, More), filters in a card, KPI section headers, section navigator
  card, chart builder toolbar with one Export menu.
- Presentation and TV modes (`pages/dashboards/Stage.tsx`): one section (or tab) per slide, sized to fill the
  screen. Presentation: arrow keys / space / buttons, Esc to leave. TV: `?tv=1&cycle=30&refresh=5` moves on by
  itself, refreshes the data, shows a clock and when the data was refreshed, larger text, hides the controls
  and pointer when idle, full screen button.
- Tests: 337 backend tests (`test_settings.py`: defaults, admin-only changes, validation, preferences
  overriding defaults, fiscal year / quarter grains and parts, week start Monday / Sunday / Saturday,
  relative fiscal periods; `test_workspaces.py`: who creates workspaces, roles shown, dashboard and chart
  access by role, Viewer role capped, row-level security unchanged for members, moving, creating directly in a
  workspace, member management and lock-out guard, deleting a workspace keeps items, deleted groups leave).
  Browser test: home, KPI dashboard and chart builder in all three themes, Arabic in Classic and Advanced,
  presentation (3 slides, keys, Esc) and TV mode (moves on by itself), settings and account pages.
- Not done: Hijri date levels in charts (months that follow the Hijri calendar) need a Hijri calendar table in
  the engine; dates are shown in Hijri, grouping stays Gregorian. TV mode uses a signed-in session (a "TV"
  user with the Viewer role works); sign-in-free screens come with signed embedding in milestone 12.

## What exists (milestone 12)

- Models (migration `0010`, `app/models/governance.py`): `item_versions`, `column_masks`, `comments`,
  `data_checks`, `subscriptions`, `alerts`, `notification_log`, `embed_keys`, `display_links`.
- **Versions** (`services/versions.py`): the state before a change is kept for dashboards and charts, at most
  the **last 2** (owner's decision); dashboards save while being edited, so a new version is kept only when
  the last one is older than 15 minutes or by someone else. Roll back keeps the current state as a version.
  Editors only; audited (`dashboard.restore`, `chart.restore`). Deleting an item deletes its versions.
- **KPI goals and scorecards** (`services/kpi.py`): a section tile can have a goal (the chart tile whose first
  measure — its grand total with dimensions — is the actual, the target, higher or lower is better, the "at
  risk" tolerance). Status (on track / at risk / off track / no data) is worked out on the server for each
  viewer with their row-level security and the filters on screen (`POST /dashboards/{id}/kpis`). Shown in
  section headers, coloured dots in the navigator, and a Scorecard dialog.
- **Comments and annotations**: comments on a dashboard or a tile, one level of replies, resolve/reopen,
  edit (author), delete (author or manager). A note pinned to a point of a chart ("Add a note here" on a
  mark) is drawn as a dashed line with its text on category charts.
- **Column masking** (`query/masking.py`): hide completely (••••), show the last 4 characters (emails keep the
  first letter and the domain) or replace with a short stable code. Applied in the query engine's `base` step
  with `SELECT * REPLACE (…)` after row-level security chose the rows, so filters, groups, calculated fields,
  previews, exports and the rows behind charts only see masked values. Admins and chosen groups see real
  values. Joined and combined datasets inherit masks (the whole combined column is masked: fail closed).
  Managed on the dataset's Security tab (owner or admin), audited.
- **Data quality and freshness checks** (`services/checks.py`): freshness (since the last sync or the newest
  date in a column), number of rows, empty values, repeats, range, allowed values. They run after every
  successful sync, every 15 minutes (freshness), on demand and when saved; the owner gets an email when a check
  starts failing; dashboards show "n data checks failing". Dataset → Quality tab.
- **Notifications** (`services/notify.py`): email (HTML + attachments), Microsoft Teams (incoming webhook
  "Workflows", adaptive card with an Open button) and WhatsApp (Business Cloud API, approved template with
  title / message / link, number from a user attribute). Admin → Settings → Notifications; webhook
  addresses and the token are encrypted and never returned; test button. Every message is logged
  (`notification_log`) and shown as "recent messages".
- **Subscriptions** (`services/delivery.py`): a dashboard on a schedule (every day, working days, weekly,
  monthly, cron) as a PDF, PNG or link, with optional filters and message. **Each recipient gets their own
  copy rendered as them** (their row-level security), plus a KPI summary; people who can no longer open the
  dashboard are skipped; Teams channels get the link only. Anyone who can view subscribes themselves;
  managers add people and groups and post to Teams. Send now.
- **Alerts**: a chart's value crosses a limit, or a KPI is off track / at risk. Checked on a schedule and after
  every sync of its datasets, **for each recipient with their own data**, sent once when it becomes true and
  again only after it was back to normal; Teams messages are checked as the owner. Same permissions as
  subscriptions. Celery beat: `lumen.dispatch_due_deliveries` (every minute), `lumen.freshness_checks` (15
  minutes); `lumen.after_sync` runs checks and alerts after a sync.
- **Signed embedding and display links** (`services/embed.py`, `docs/EMBEDDING.md`): embed keys (admins;
  secret shown once; allowed dashboards) for tokens signed by another application (HS256, `kid`, at most 24
  hours, an active user who can open the dashboard); display links for TV screens (run as a chosen non-admin
  user; hash stored; expiry; revoke). Both become a short embed session sent in the `X-Lumen-Embed` header that
  only the read endpoints of that one dashboard accept (`deps.dashboard_viewer`); revoking ends sessions at
  once. Pages `/embed/:id#token=…` and `/display#…` (TV mode, session renewed). Caddy allows framing
  `/embed/*` only by `EMBED_FRAME_ANCESTORS`; every other page `frame-ancestors 'self'`.
- Arabic for every new screen (about 1,270 texts in total).
- Tests: 362 backend tests on PostgreSQL (`test_governance.py`: versions keep 2 and roll back, permissions,
  KPI status per viewer's rows, goal validation, comments and permissions, masking never reaches others in
  queries / rows / calculated fields / exports, methods and exempt groups, inherited masks, data checks and
  emails; `test_delivery.py`: channel settings hide secrets, per-recipient subscriptions rendered as each
  person, skipped recipients, schedule, value and KPI alerts per recipient and once per change, WhatsApp and
  Teams calls, permissions, display links and embed keys: read only, one dashboard, refused tokens, revoking).
  Browser test: KPI chips and navigator, scorecard, comments, annotation line, subscription sent with a
  rendered PDF, KPI alert happening, versions, data checks, masking editor, a display link and an embed token
  opened with no sign-in (other API calls refused), Arabic.

## Next: milestone 13 (AI + MCP)

- Ask Data (English/Arabic), auto-dashboard, explain, forecasting, anomaly alerts, formula helper, AI daily
  digest; cloud and self-hosted providers with routing for sensitive datasets.
- Lumen MCP server (remote, OAuth per user, read-only, RLS, audited) so Claude can search datasets and
  dashboards and run queries.

## Open questions for the owner

- Self-hosted AI: is there (or will there be) a GPU server to run the local model?
- Hosting: on-premise or UAE cloud region?
- First Salesforce objects and Oracle schemas?
- WhatsApp: which Meta business account and approved template name to use (the template needs three values:
  title, message, link)?
