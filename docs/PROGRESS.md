# Progress

## Status

| # | Milestone | Status |
| --- | --- | --- |
| 1 | Foundation (repo, Docker, CI, migrations) | Done |
| 2 | Auth, users, roles, groups, audit | Done (TOTP two-factor deferred to milestone 12) |
| 3 | SSO: Microsoft Entra ID, UAE PASS | Next |
| 4 | Connector framework + files + Oracle | — |
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
- AI provider: not yet confirmed by the owner; plan is provider-agnostic with Claude API as default.

## Next: milestone 3 (SSO)

- `identity_providers` table + admin settings page (client id/secret encrypted, endpoints, claim mapping,
  invite-only vs auto-create with default role).
- Authlib OIDC flow: `/auth/sso/{provider}/start` and `/callback`, state + nonce + PKCE.
- Microsoft Entra ID: groups claim → groups (source "entra"), department/office → attributes.
- UAE PASS: staging/production endpoints, scope `openid urn:uae:digitalid:profile:general`, `sub` as id.
- Account linking by verified email or external id; SSO buttons on the login page.
- Needs from the owner for real testing: Entra app registration; UAE PASS staging client credentials.

## Open questions for the owner

- AI: Claude/OpenAI API or self-hosted model?
- Hosting: on-premise or UAE cloud region?
- Arabic interface from day one?
- First Salesforce objects and Oracle schemas?
