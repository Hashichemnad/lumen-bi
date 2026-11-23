# Admin guide

For the people who install and run Lumen. Installation is in the [README](../README.md).

## First day

1. Sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD` and change the password (My account).
2. **Admin → Settings**: regional settings (calendar, weekend, fiscal year), **Branding** (name, logo,
   colour, sign-in message), **Email** (mail server; **Send a test to me**), **Two-factor sign-in** policy.
3. **Admin → Sign-in methods**: Microsoft Entra ID and UAE PASS ([SSO_SETUP.md](SSO_SETUP.md)).
4. **Admin → Users / Groups**: add people one by one or import a CSV; set attributes (e.g. `region`) used by
   row-level security.
5. **Workspaces**: one per department; add managers, editors and viewers.
6. **Data**: connect the first systems ([CONNECTORS.md](CONNECTORS.md)), then set each dataset's Security tab
   (who can use it, row-level security, masking, sensitive).

## Roles

| Role | Can |
| --- | --- |
| Viewer | Open dashboards shared with them, filter, subscribe, comment |
| Explorer | + build charts and dashboards from data shared with them, export, Ask Data and Explain |
| Creator | + connections, datasets, modelling, row-level security of their datasets |
| Admin | Everything, including users, settings, sign-in methods, AI and backups; sees all rows |

## Two-factor sign-in

People turn it on in **My account → Two-factor sign-in** (an authenticator app on their phone, plus ten
recovery codes). In **Admin → Settings → Two-factor sign-in** you can require it for admins or for
everyone who signs in with a password; they set it up at their next sign-in. Single sign-on users are not
affected (Microsoft and UAE PASS do their own checks).

Someone lost their phone and their recovery codes: **Admin → Users → the person → Reset two-factor**. It is
turned off and they are signed out; if it is required, they set it up again at the next sign-in.

## Keeping an eye on things

- **Admin → Usage**: active people per day, most and least opened dashboards, exports, AI and Claude
  requests, accounts by role. Reports rendered for email subscriptions are not counted as views.
- **Sync monitor** (Data → Sync monitor): every dataset's refreshes over 7 days — failed and late first, the
  last error, usual duration — with **Retry**. The dataset's owner also gets an email when a sync or a data
  check fails.
- **Dataset → Lineage**: where the data comes from and everything built on it (datasets, charts,
  dashboards, subscriptions, alerts). Choose a field to see what uses it before renaming or removing it.
- **Admin → Audit log**: sign-ins, changes, exports, previews as another person, AI and MCP requests, settings
  and backups.

## Backups

Admin → Settings → **Backups**: a backup every night (choose the hour; default 02:00) and the newest 7 kept,
in the `lumen-data` volume under `backups/`. **Back up now** makes one; **Download** to keep copies off the
server (do this regularly, or copy the folder with your normal server backup).

A backup holds the whole metadata database (users, settings, connections, datasets, dashboards, charts,
audit log…) and the current data of every dataset plus uploaded files. Connection passwords and other
secrets inside stay encrypted with `ENCRYPTION_KEY`: **store `.env` (or at least `ENCRYPTION_KEY` and
`SECRET_KEY`) safely, separately from the backups**. Without the key a restored Lumen cannot reach its data
sources.

For very large installations, also back up PostgreSQL with `pg_dump` from the `postgres` container.

### Restore

Restoring replaces everything with the backup. Use the same Lumen version that made it.

```bash
docker compose stop api worker beat
# a backup from elsewhere: put it in the data volume first
docker compose run --rm -v "$PWD/lumen-20261123-220000.tar.gz:/tmp/b.tar.gz" api \
    python -m app.backup restore /tmp/b.tar.gz --yes
# or one already on the server:
docker compose run --rm api python -m app.backup restore lumen-20261123-220000.tar.gz --yes
docker compose start api worker beat
```

Everyone needs to sign in again afterwards if `SECRET_KEY` changed.

## AI and Claude

See [AI.md](AI.md): models (cloud or your own servers), sensitive data, features, comparing models, and
connecting Claude through the MCP server.

## Updating

```bash
git pull
docker compose up --build -d    # migrations run automatically when the API starts
```

Make a backup first (**Back up now**). Release notes are in [PROGRESS.md](PROGRESS.md).

## Limits and settings

All in `.env` ([.env.example](../.env.example) explains each): query limits, export size, DuckDB memory,
cache, upload size, snapshots kept, sign-in lifetimes, rate limits (`LOGIN_PER_MINUTE`), AI request limits,
MCP token lifetimes. The security review is in [SECURITY_REVIEW.md](SECURITY_REVIEW.md); performance in
[LOADTEST.md](LOADTEST.md).
