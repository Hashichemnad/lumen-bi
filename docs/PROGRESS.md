# Progress

## Status

| # | Milestone | Status |
| --- | --- | --- |
| 1 | Foundation (repo, Docker, CI, migrations) | Done |
| 2 | Auth, users, roles, groups, audit | Done (TOTP two-factor deferred to milestone 12) |
| 3 | SSO: Microsoft Entra ID, UAE PASS | Done (real Microsoft/UAE PASS sign-in still to be tried by the owner) |
| 4 | Connector framework + files + Oracle | Next |
| 5 | Salesforce and REST connectors | — |
| 6 | Semantic layer | — |
| 7 | Query engine + row-level security | — |
| 8 | Chart builder UI | — |
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

## Next: milestone 4 (connector framework + files + Oracle)

- Connector interface (`test_connection`, `list_objects`, `get_schema`, `extract` as Arrow batches).
- `connections` and `datasets` tables; secrets encrypted with `app/core/crypto.py`.
- Parquet snapshot store (`DATA_DIR/datasets/<id>/v<n>/`), atomic switch, keep last 3.
- Sync runs table, Celery task, Celery Beat schedules from dataset settings, failure emails.
- File upload connector (CSV/Excel/Parquet) and Oracle (python-oracledb thin mode; table/view/SQL;
  incremental by column). Oracle testing needs a reachable Oracle instance (owner's network, or a container).
- Frontend: Data → Connections and Datasets pages, sync history, preview.

## Open questions for the owner

- Self-hosted AI: is there (or will there be) a GPU server to run the local model?
- Hosting: on-premise or UAE cloud region?
- Arabic interface from day one?
- First Salesforce objects and Oracle schemas?
