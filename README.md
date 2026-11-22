# Lumen BI

Self-hosted business intelligence: connect company systems (Salesforce, Oracle, REST APIs, files), sync
their data, and build interactive dashboards where each person sees only the data they are allowed to see.

> Status: milestones 1–2 of 12 are done (foundation, sign-in, users, roles, groups, audit log).
> See [docs/PROGRESS.md](docs/PROGRESS.md) and the spec in [docs/SPEC.md](docs/SPEC.md).

## Run it with Docker (recommended)

You need Docker Desktop (Windows/macOS) or Docker Engine with the Compose plugin (Linux).

```bash
git clone https://github.com/Hashichemnad/lumen-bi.git
cd lumen-bi
cp .env.example .env          # then edit .env (see below)
docker compose up --build -d
```

Open http://localhost:8080 and sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`.
The first admin is created only on the very first start.

The first build takes a few minutes (it downloads images and packages). Later starts take seconds.

**Before using it for real, change these in `.env`:**

| Setting | How |
| --- | --- |
| `SECRET_KEY` | `openssl rand -hex 32` |
| `ENCRYPTION_KEY` | `openssl rand -base64 32` |
| `POSTGRES_PASSWORD` | any long random password |
| `ADMIN_PASSWORD` | at least 10 characters with a letter and a number |
| `SMTP_*` | your mail server, for password-reset and invite emails |

### Going live on a server with a domain

1. Point a DNS record (e.g. `bi.yourcompany.ae`) at the server.
2. In `.env`: `SITE_ADDRESS=bi.yourcompany.ae`, `HTTP_PORT=80`, `PUBLIC_URL=https://bi.yourcompany.ae`,
   `COOKIE_SECURE=true`.
3. `docker compose up -d`. Caddy gets an HTTPS certificate automatically (ports 80 and 443 must be open).

### Everyday commands

```bash
docker compose ps                 # what is running
docker compose logs -f api        # API logs (emails are printed here when SMTP is not set)
docker compose pull && docker compose up --build -d   # update after `git pull`
docker compose down               # stop (data is kept in Docker volumes)
```

Backup the metadata database:

```bash
docker compose exec postgres pg_dump -U lumen lumen > backup-$(date +%F).sql
```

## Develop without Docker

Backend (Python 3.12+):

```bash
cd backend
pip install -e ".[dev]"
alembic upgrade head && python -m app.bootstrap   # SQLite in ./data by default
uvicorn app.main:app --reload                     # API docs: http://localhost:8000/api/docs
pytest -q
```

Frontend (Node 22+):

```bash
cd frontend
npm ci
npm run dev                                       # http://localhost:5173
```

## Importing users

Admin → Users → Import CSV. Columns: `email` (required), `name`, `role` (viewer, explorer, creator, admin),
`groups` (separated by `;`), `password`, and `attr_<name>` for data-access attributes, e.g.:

```csv
email,name,role,groups,attr_region,attr_branch
sara@company.ae,Sara Ahmed,creator,Finance;Sales,UAE;KSA,Dubai
```

## Project layout

```
backend/    FastAPI API, Celery worker, Alembic migrations, tests
frontend/   React + TypeScript web app
deploy/     Caddy (HTTPS + static files) and its Dockerfile
docs/       Spec and progress notes
```
