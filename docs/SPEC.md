# Lumen BI — System specification (repo copy)

The full, living spec, including the 38-row Tableau comparison and the architecture diagram, is the doc
"Lumen BI — System Specification & Build Plan" (shared by the project owner). This file keeps the parts
needed while coding. If the two disagree, ask the owner.

## Goal

A self-hosted business intelligence platform: connect company systems, sync their data, and build
Tableau-style interactive dashboards that show each user only the data they are allowed to see.

## Confirmed decisions

| Topic | Decision |
| --- | --- |
| Users | 100+ named users, up to 20 at the same time |
| Day-one connectors | Salesforce, Oracle, generic REST/JSON APIs, plus CSV/Excel upload |
| Login | Email + password, Microsoft Entra ID (OIDC), UAE PASS (OAuth 2.0 / OIDC) |
| Delivery | All features in one release, built in ordered milestones |
| AI provider | Both: cloud API (Claude or OpenAI) and a self-hosted model (OpenAI-compatible server such as Ollama or vLLM), configured by the admin |
| Hosting | Owner's server with Docker Compose |

Out of scope: desktop app, offline mobile app, Tableau Prep-style flows, multi-tenant SaaS.

## Architecture

Browser → Caddy (HTTPS, static frontend, `/api` proxy) → FastAPI API (auth, semantic layer, query engine,
row-level security, AI service). Celery workers (scheduled sync, reports, alerts) pull from data sources into
**Parquet snapshots** queried by **DuckDB**. PostgreSQL holds metadata; Redis is cache + job queue.

Each sync writes a new immutable snapshot (`datasets/<id>/v<n>/`) and switches atomically, so readers never
block writers and a failed sync never corrupts live data. Keep the last 3 snapshots.

## Tech stack

React 19 + TypeScript + Vite · Tailwind CSS · Apache ECharts · react-grid-layout + dnd-kit · TanStack Query ·
Python 3.13 + FastAPI + Pydantic v2 · SQLAlchemy 2 + Alembic · PostgreSQL 17 · DuckDB · Celery + Redis ·
Authlib (OIDC) · Argon2 · JWT in httpOnly cookies · simple-salesforce (REST + Bulk API 2.0) ·
python-oracledb (thin mode) · httpx · Playwright (PDF/PNG reports) · Docker Compose + Caddy · pytest · Ruff.

## Features (all in this release)

1. **Users and access**: roles Admin / Creator / Explorer / Viewer; groups; user attributes for RLS;
   dashboard sharing (private, users/groups, everyone; view/edit); workspaces with permissions; audit log;
   Arabic + English with RTL.
2. **Connections**: Salesforce (OAuth), Oracle, REST/JSON, CSV/Excel/Parquet; encrypted credentials;
   connection test; connector plug-in interface.
3. **Sync**: full or incremental (modified-date column); schedules (N minutes, hourly, daily, cron) + manual;
   history, retries with back-off, rate-limit handling, failure emails; optional live mode for Oracle.
4. **Modelling**: column types and dimension/measure roles, labels, formats (AED, %, dates); calculated
   fields; joins/unions; date and custom hierarchies; groups, bins, sets; parameters.
5. **Chart builder**: shelves (Columns, Rows, Color, Size, Label, Tooltip, Detail, Filters); Show Me;
   ~20 chart types (bar, line, area, combo/dual axis, pie/donut, scatter/bubble, heatmap, highlight table,
   pivot, table, KPI card, gauge, funnel, treemap, waterfall, box plot, histogram, filled + point maps with
   UAE emirates/GCC/world); aggregations incl. median/percentiles; quick table calcs (running total,
   % of total, difference, % change, rank, moving average); top-N, reference lines, trend lines, totals;
   conditional formatting.
6. **Dashboards**: drag/resize grid; text/image/web blocks; global filters (dropdown, multi-select, search,
   date range, relative dates, slider); cross-filtering; drill down/up and drill-through; tooltips; view
   underlying rows (RLS applied); tabs; device layouts; export PNG/PDF/CSV/Excel; links keep filters;
   per-recipient scheduled email subscriptions; data-driven alerts; signed embedding; favourites, comments.
7. **AI**: Ask Data (English/Arabic) → chart; auto-dashboard; explain; anomaly alerts; forecasting; formula
   helper. AI only produces a query spec that runs through the same RLS; only schema and aggregated results
   are sent to the model.
   - **Providers (both supported)**: cloud (Anthropic Claude API, OpenAI API) and self-hosted (any
     OpenAI-compatible endpoint: Ollama, vLLM, LM Studio). Admin configures one or more and picks a default.
   - **Routing**: a dataset can be marked *sensitive*; AI requests about it go only to a self-hosted
     provider, and fail with a clear message if none is configured. Admin can also restrict features
     (e.g. Explain on cloud, Ask Data on local).
   - Same test set of sample questions runs against each configured provider to compare accuracy.
   - Forecasting and anomaly detection are statistical (run on the server), so they need no AI provider.
8. **Admin**: users/groups/attributes, CSV import; connection and sync monitor; usage analytics; settings
   (SSO, SMTP, AI provider, branding); backup/restore.
9. **Platform**: public REST API with OpenAPI docs; health checks; structured logs; versioned migrations.

## Authentication

- Local accounts: Argon2, policy (≥10 chars, letter + number), lockout, email reset, optional TOTP.
- Microsoft Entra ID (OIDC): groups → Lumen groups; claims → attributes.
- UAE PASS: authorization code flow; staging and production endpoints (authorize, token, userinfo, logout);
  scope `openid urn:uae:digitalid:profile:general`; identify by `sub`; name/email from `fullnameEN`, `email`.
  Requires service-provider onboarding via the UAE PASS Developer Portal (https://docs.uaepass.ae).
- Per provider: invite-only or just-in-time creation with a default role. Link accounts by verified email or
  stored `sub` / Entra object ID.
- Sessions: short access token + refresh token in httpOnly cookies; CSRF via required custom header.

## Row-level security (must never leak)

1. Rules per dataset: `column` matches a user attribute (`Region = user.region`, `Branch IN user.branches`),
   or an **entitlement table** (synced dataset mapping user → allowed values).
2. Applied as a WHERE clause inside the query engine before filters, aggregation, exports, filter-value lists
   and AI queries. Never in the browser.
3. Rules on a dataset combine with AND. No matching attribute → no rows (fail closed). Admins bypass.
4. "Preview as user" for checking. Cache keys include the security context.

Object-level permissions (workspace, dashboard, dataset, connection: view / edit / manage) sit on top.

## Connectors

Interface: `test_connection()`, `list_objects()`, `get_schema(object)`, `extract(object, since=None)` (streams
Arrow record batches), `live_query(sql)` (optional).

| Connector | Reads with | Incremental by |
| --- | --- | --- |
| Salesforce | REST for small objects, Bulk API 2.0 for large, SOQL; deletions via `queryAll` | `SystemModstamp` / `LastModifiedDate` |
| Oracle | python-oracledb thin; table, view or SQL; optional live mode | chosen date/number column |
| REST/JSON | endpoint, method, headers, params; auth API key/Bearer/Basic/OAuth2 client credentials; pagination page/offset/cursor/next-link; JSONPath to records; flatten nested JSON | date param or cursor |
| Files | CSV, Excel (each sheet), Parquet; upload or URL on schedule | re-upload replaces |

Sync pipeline: Beat queues job → worker extracts to new snapshot → incremental merge by primary key →
schema-change detection → validate → atomic switch → clear cache → log run → email on failure.

## Query spec (single format used by UI, AI, exports, subscriptions)

```json
{"dataset": 12, "dimensions": [{"field": "order_date", "grain": "month"}, "region"],
 "measures": [{"field": "amount", "agg": "sum"}], "filters": [{"field": "status", "op": "in", "values": ["Won"]}],
 "table_calc": {"type": "pct_change"}, "sort": [{"field": "amount", "dir": "desc"}], "limit": 5000}
```

Every field is validated against the semantic layer and compiled to parameterised SQL (never string
concatenation of user input), RLS added, then run on DuckDB.

## Performance targets (one server: 8 vCPU, 32 GB RAM, SSD)

Dashboard of 8 charts < 1 s cached, < 3 s uncached on 10M rows; single chart query < 500 ms on 10M rows;
cross-filter < 1 s; 20 concurrent users without slowdown, load-tested to 50.

## Milestones

1. Foundation: monorepo, Docker Compose, Alembic, CI
2. Auth and users: local login, roles, groups, attributes, sessions, admin screens, audit log
3. SSO: Microsoft Entra ID, UAE PASS (staging), claim mapping, account linking
4. Connector framework + files + Oracle: interface, encrypted secrets, Parquet snapshots, sync runs, schedules
5. Salesforce and REST connectors
6. Semantic layer: fields, formats, calculated fields, joins, hierarchies, parameters, groups, bins
7. Query engine + RLS: compiler, table calcs, caching, preview-as-user, security test suite
8. Chart builder UI
9. Dashboards UI: grid, filters, cross-filtering, drill, tabs, device layouts, sharing
10. More chart types + exports: pyramid (+stacked), parallel coordinates, radar, time axis (timeline, time
    bar, time combo with zoom), calendar heat map, Gantt, gauges (gauge, polar, flat), bullet, rating, dot
    plots, waterfall (+stacked), funnel, Origami, Sankey; Geo Map with uploaded boundary files (emirates,
    districts, territories); dashboard sections with a section navigator (KPI dashboards); dashboard export
    to PDF/PNG and data to Excel
11. UI/UX redesign: new design for every screen and dashboards, three themes (Classic, Advanced dark,
    Tableau-style), Arabic + English with RTL, workspaces with permissions, Hijri calendar / UAE weekend /
    fiscal year settings, TV/kiosk and presentation modes
12. Subscriptions, alerts, embedding and governance: per-recipient email subscriptions, data-driven alerts,
    Teams/email/WhatsApp notifications, signed embedding, comments and chart annotations, column masking
    (PII), KPI goals and scorecards (targets, status in section headers), data quality and freshness checks,
    versions (last 2 kept) with roll back
13. AI + MCP: Ask Data (English/Arabic), auto-dashboard, explain, forecasting, anomaly alerts, formula helper,
    AI daily digest; Lumen MCP server (remote, OAuth per user, read-only, RLS, audited) so Claude and other
    MCP clients can search datasets and dashboards and run queries
14. Admin and hardening: usage analytics, settings, backup/restore, lineage and impact view, data entry
    (write-back of targets and comments, if wanted), OWASP review, load test, guides
