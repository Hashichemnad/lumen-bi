# AI in Lumen

Lumen's AI features help people ask questions, explain charts, write formulas and start dashboards. The MCP
server lets Claude (and other AI apps) read Lumen as the signed-in person. This page is for admins.

## What the AI sees

- Field names, labels, descriptions and types of the dataset in question.
- Up to 15 common values of up to 12 text fields, and the date range of date fields — **read as the person
  asking**, so their row-level security and column masks apply. Masked columns are never sampled.
- Aggregated results of the queries it proposed (at most 200 rows), for the short answer or the explanation.

It never sees raw rows, passwords, connection details or other people's data. The prompt and answer are not
stored; `ai_requests` keeps who, which feature, which model, tokens, time and errors.

The model only proposes field names and operators. Lumen checks every field against the dataset, builds the
query itself (values are bound, never pasted into SQL) and runs it with the person's row-level security.
A field that does not exist is sent back to the model once to fix; then the request fails with a clear
message.

## Models (Admin → AI)

| Kind | Address | Key |
| --- | --- | --- |
| Anthropic (Claude) | `https://api.anthropic.com` (leave empty) | Anthropic API key. Suggested model: `claude-sonnet-5-5` |
| OpenAI | `https://api.openai.com/v1` (leave empty) | OpenAI API key |
| Self-hosted | Any OpenAI-compatible address ending in `/v1`: Ollama `http://ollama:11434/v1`, vLLM, LM Studio, Azure OpenAI | Only if the server needs one |

- **Test** sends a one-word request to check the address, key and model name.
- **Default model**: used first. Otherwise the oldest model that fits.
- **Use it for**: restrict a model to some features (for example Explain on the cloud model, Ask Data on the
  local one). Empty = all features.
- Keys are encrypted with `ENCRYPTION_KEY` and never returned by the API or written to the audit log.
- `AI_HOURLY_LIMIT` (default 60) caps requests per person per hour; `AI_TIMEOUT_SECONDS` (default 90).

### Running a local model

`docker compose --profile ai up -d` starts Ollama next to Lumen. Pull a model, then add it as
**Self-hosted**, address `http://ollama:11434/v1`, and tick **Runs on our own servers**:

```bash
docker compose exec ollama ollama pull qwen2.5:14b   # good with JSON and Arabic; needs ~10 GB of memory
```

A GPU makes answers much faster. Larger models follow the instructions better; use **Compare models** to
check.

## Sensitive data

On a dataset's **Security** tab, mark it **Sensitive**. Then AI requests about it go **only** to models marked
**Runs on our own servers** (only self-hosted models can be marked so). Datasets built from it (joins and
unions) are sensitive too. If no such model is set up, the feature says so and nothing is sent anywhere — Lumen
never falls back to a cloud model.

When someone asks a question without choosing a dataset and no self-hosted model exists, sensitive datasets
are left out of the choice.

## Features

| Feature | Who | Where |
| --- | --- | --- |
| Ask Data (English and Arabic) | Explorers and up | Home; "Describe the chart you want" in the chart builder |
| Explain | Explorers and up | Chart builder; a dashboard tile's ⋮ menu |
| Formula helper | Creators | Calculated field → Write it for me (the formula is checked before it is offered) |
| Build a dashboard | Explorers and up | Dashboards → Build with AI (4–8 charts; each is checked and run before it is kept) |
| Daily summary | Everyone (opt in) | My account → Daily summary: from 7:00 each day, the KPIs of favourite dashboards, read with the person's access; without an allowed model the email has just the numbers |
| Compare models | Admins | Admin → AI: the same questions on each model, with the fields and chart each should pick |

**Forecasts** (chart builder → Options → Forecast, line and area charts over a date) and **anomaly alerts**
(Alerts → Something unusual happens) are statistics on the server. They need no AI model:

- Forecast: Holt-Winters (trend and season; 12 months, 4 quarters, 7 days, 52 weeks) when there are two
  seasons of history, otherwise Holt's trend; at least 4 periods. The period that is not over yet is left out
  of the fit. The shaded band is the likely range (80–99%). Each series (Color) is forecast on its own.
- Anomaly: the latest finished period of the chart's first measure is compared with up to 30 periods before
  it using the median and median absolute deviation. Sensitivity high / medium / low = a robust score of
  2.5 / 3.5 / 5. Checked for each recipient with their own data, like other alerts.

## Connecting Claude (MCP)

1. Admin → AI → **Connect Claude and other AI apps**: turn it on and choose who may connect (default
   Explorers and up).
2. Each person, in Claude: **Settings → Connectors → Add custom connector**, paste the server address
   (`https://<your Lumen address>/mcp`). Claude registers itself, opens Lumen's sign-in and consent page; the
   person signs in (any of the usual sign-in methods) and clicks **Allow**.
3. Claude can now use these read-only tools as that person: `search`, `describe_dataset`, `run_query`
   (aggregated; raw rows for Explorers and up), `get_dashboard`, `query_dashboard_tile`, `get_kpis`.

For custom connectors in Claude (web and desktop), Claude's servers connect to Lumen, so Lumen must be
reachable from the internet over HTTPS. Claude Code connects from the person's own computer
(`claude mcp add --transport http lumen https://<your Lumen address>/mcp`), so an internal address works there.

Security:

- OAuth 2.1: dynamic client registration (public clients, redirect addresses must be https or localhost),
  authorization code with PKCE S256 only, tokens bound to this server (`resource`).
- Access tokens last `MCP_ACCESS_TOKEN_MINUTES` (60); refresh tokens `MCP_REFRESH_TOKEN_DAYS` (30) and rotate
  on every use. A refresh token used twice disconnects that app for that person. Tokens are stored only as
  hashes and only work on `/mcp` (not on the rest of the API).
- Every tool call is in the audit log (`mcp.tool`, with the tool, the app and the dataset or dashboard);
  connecting and disconnecting too.
- People see and disconnect their apps in **My account → Connected AI apps**; admins see everyone's in
  Admin → AI. Turning the server off stops existing tokens at once.
- Endpoints: `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`,
  `/oauth/register`, `/oauth/token`, `/oauth/revoke`, `/mcp` (Streamable HTTP, JSON responses, no sessions),
  and the consent page `/oauth/authorize`. Caddy sends these to the API.
