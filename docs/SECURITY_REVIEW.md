# Security review (OWASP Top 10, milestone 14)

How Lumen handles each OWASP Top 10 (2021) risk, what was checked, and what changed in this review. Data
access rules (row-level security, masking, sensitive data) are described in `SECURITY.md` and `AI.md`.

| Risk | How Lumen handles it | Checked by |
| --- | --- | --- |
| A01 Broken access control | Every endpoint declares the role it needs; dashboards, charts, datasets and workspaces check view / edit / manage on the server. Row-level security and masking run inside the query engine for every read (charts, exports, filter lists, previews, AI, MCP, email reports). Fail closed: no matching value = no rows. IDs of things a person cannot open answer 404. | Permission tests for every endpoint (which roles are refused), RLS tests in every area |
| A02 Cryptographic failures | Passwords: Argon2id. Secrets (connection passwords, SSO client secrets, AI keys, SMTP password, Teams/WhatsApp tokens, two-factor secrets): Fernet with `ENCRYPTION_KEY`. Tokens for resets, embed keys, display links, OAuth codes and tokens, recovery codes: stored as SHA-256 hashes. HTTPS by Caddy; HSTS. | Tests that secrets are never returned or written to the audit log |
| A03 Injection | SQL is built from a parsed formula / query spec: fields are validated against the semantic layer and quoted, values are bound. Connector SQL (Oracle, PostgreSQL) is read-only and run by the source's own permissions. CSV exports neutralise formula-looking cells. | Formula and query tests with hostile names and values |
| A04 Insecure design | Least privilege by default (new users are Viewers; datasets are private until shared; MCP and AI off until an admin turns them on). AI models only propose field names; Lumen validates them and runs the query with the person's RLS. Sensitive data never reaches cloud AI. | Design reviews per milestone (`PROGRESS.md`) |
| A05 Security misconfiguration | Caddy sends a strict Content-Security-Policy (scripts only from Lumen), `X-Frame-Options`/`frame-ancestors` (only `/embed/*` can be framed, by listed sites), `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS. **New:** the API refuses to start over HTTPS with the sample `SECRET_KEY` or without `ENCRYPTION_KEY`; cookies are always `Secure` when `PUBLIC_URL` is https. API docs are at `/api/docs` (they list endpoints, not data). | Browser test with the CSP applied |
| A06 Vulnerable components | **New:** `pip-audit` and `npm audit` (production packages) run on pull requests to main. At this review: no known vulnerabilities in either. | CI `audit` job |
| A07 Identification and authentication failures | Password policy (10+ characters, letter and number), lockout after 5 failures, equal timing for unknown emails, reset links that expire and work once, sign out everywhere. **New:** two-factor sign-in (authenticator app, recovery codes; admins can require it for admins or everyone), rate limits on sign-in, two-factor codes, password reset, OAuth and embed sessions. SSO: Entra ID and UAE PASS with state and PKCE. | `test_auth.py`, `test_twofactor.py`, `test_sso.py` |
| A08 Software and data integrity failures | Embed tokens are signed (HS256 with a per-key secret, `kid`, at most 24 hours). Migrations are versioned and tested up and down. Backups include a manifest with the database version; restore refuses a different version. | `test_delivery.py`, `test_admin.py` |
| A09 Logging and monitoring failures | Audit log of sign-ins (and failures), every change, exports, previews as another user, AI requests (without prompts), MCP tool calls, settings and backups. Sync and data-check failures email the owner; the sync monitor shows them. | Audit assertions in tests |
| A10 Server-side request forgery | REST connectors refuse loopback, link-local (cloud metadata) and reserved addresses (unless `ALLOW_LOCAL_API_URLS` for development) and check every redirect again; company-internal addresses are allowed on purpose, as internal APIs are a normal source. Only admins set AI model and SMTP addresses (self-hosted models are expected on the internal network). Images and web tiles are loaded by the browser, not the server. | `test_api_connectors.py` |

## Changes made in this review

- Two-factor sign-in (TOTP) with recovery codes, an organisation policy, and admin reset.
- Rate limits per address (Redis, shared by all API workers; memory if Redis is down).
- Content-Security-Policy and Permissions-Policy, HSTS.
- Startup refuses sample secrets on HTTPS installs; `Secure` cookies follow `PUBLIC_URL`.
- The two-factor QR code is shown as an image (no HTML from the server is inserted into the page).
- Dependency audit in CI.

## Still to do before go-live (owner)

- Set strong `SECRET_KEY` and `ENCRYPTION_KEY` (and keep `ENCRYPTION_KEY` with the backups: without it the
  secrets in a backup cannot be read).
- Serve Lumen over HTTPS (`SITE_ADDRESS` = the real domain).
- Decide the two-factor policy (Admin → Settings).
- An external penetration test is recommended before opening Lumen to the internet (needed for Claude.ai).
