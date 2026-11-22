# Lumen BI — instructions for Claude

Self-hosted BI platform (Tableau-style dashboards, connectors, row-level security, AI).

**Before any task, read `docs/SPEC.md` (what to build) and `docs/PROGRESS.md` (what is done, decisions,
what is next).** Update `docs/PROGRESS.md` at the end of every milestone or significant change.

## Layout

- `backend/` — FastAPI app (`app/`), Alembic migrations (`migrations/`), pytest tests (`tests/`)
  - `app/api/v1/` routers · `app/api/deps.py` auth/role/CSRF dependencies · `app/models/` ORM ·
    `app/schemas.py` Pydantic models · `app/services/` business logic · `app/worker.py` Celery
- `frontend/` — React 19 + TypeScript + Vite + Tailwind; `src/lib/api.ts` is the only way to call the API
- `deploy/` — Caddyfile and web Dockerfile · `docker-compose.yml` at the root

## Commands

```bash
# backend (from backend/)
pip install -e ".[dev]"
ruff format . && ruff check .
pytest -q                                   # SQLite
TEST_DATABASE_URL=postgresql+psycopg://... pytest -q   # PostgreSQL, as CI does
alembic revision --autogenerate -m "..."    # after model changes; review the file, name it 000N_*.py
uvicorn app.main:app --reload               # http://localhost:8000/api/docs

# frontend (from frontend/)
npm ci && npm run lint && npm run build
npm run dev                                 # http://localhost:5173, proxies /api to :8000

# everything
docker compose up --build                   # http://localhost:8080
```

## Rules

- Every endpoint gets tests, including permission tests (which roles are refused).
- Every model change gets an Alembic migration that upgrades and downgrades on PostgreSQL.
- Row-level security is enforced only on the server, in the query engine. Never trust the browser.
  Fail closed. Any change near RLS needs tests proving users cannot see other users' rows.
- Never build SQL by concatenating user input; validate fields against the semantic layer and bind values.
- Secrets never go in the repo, in logs, or in API responses. Connection secrets are encrypted at rest.
- Record security-relevant actions with `services.audit.record`.
- Cookie-authenticated writes require the `X-Requested-With: lumen` header (`csrf_protect`); the
  frontend `api()` helper adds it.
- Keep CI fast: lint + unit tests on every push; heavy jobs (Docker build, browser tests) only on PRs to main.
- Python: typed, Ruff-clean, line length 110. TypeScript: strict, no `any` unless unavoidable.
- UI text: short, plain English (Arabic translations come with the i18n milestone).
