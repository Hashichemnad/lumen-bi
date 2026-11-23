# Embedding dashboards and display links

Two ways to show a dashboard without a Lumen sign-in. Both open **one dashboard, read only, as one Lumen
user**: that user's row-level security, column masking and dashboard access apply. Nothing else in Lumen can be
reached with them.

## Display links (TV screens)

On the dashboard: **More → Display links** (admins). Choose a name, the person whose data the screen shows (for
example a "TV screen" user with the Viewer role and only the access the screen needs) and, if wanted, an expiry.
The link is shown once:

```
https://lumen.example.ae/display#<token>
```

It opens the dashboard in TV mode and keeps working (the session renews itself) until it expires or is
revoked. Revoking stops screens at once. Admins cannot be chosen: they see every row.

## Embed keys (inside another application)

**Admin → Settings → Embedding**: create a key and choose which dashboards it may open. The secret is shown
once; store it in the other application's server (never in a browser).

The application signs a token for each page view (HS256, header `kid` = the key's id):

```python
import jwt, datetime

token = jwt.encode(
    {
        "dashboard": 12,                       # one of the key's dashboards
        "user": "sara.ahmed@company.ae",       # an active Lumen user who can open it
        "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=10),  # at most 24 hours
    },
    LUMEN_EMBED_SECRET,
    algorithm="HS256",
    headers={"kid": "3"},
)
iframe_src = f"https://lumen.example.ae/embed/12#token={token}"
```

```html
<iframe src="…" style="width:100%;height:900px;border:0" title="Sales dashboard"></iframe>
```

The token is in the part after `#`, so it is not sent to servers or written to logs. Lumen exchanges it for a
one-hour session that only reads that dashboard. Turning a key off ends its sessions at once. Refused tokens are
recorded in the audit log (`embed.refused`).

### Allowing the frame

Browsers only show Lumen inside a page of another site if Lumen allows it. Set the sites in `.env`:

```
EMBED_FRAME_ANCESTORS=https://portal.company.ae https://intranet.company.ae
```

Only `/embed/...` pages may be framed by those sites; every other Lumen page may only be framed by Lumen itself.
