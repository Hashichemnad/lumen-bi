# Look, language, calendar, workspaces and TV screens

## Themes

| Theme | For |
| --- | --- |
| Classic (default) | Light and calm, with roomy cards |
| Advanced | Dark, with large numbers; good for control rooms and TV screens |
| Analyst | Compact and dense, like analyst tools (Tableau-style: square corners, blue and green field pills) |

Admins choose the default for everyone in **Admin → Settings**. Each person can choose their own in
**My account → Appearance**, or go back to the organisation's default.

For developers: screens use the `slate`, `brand` and status colour scales of Tailwind; each theme redefines
them in `frontend/src/index.css` (`:root[data-theme="…"]`). Charts take their colours from
`frontend/src/lib/chart/palette.ts`, which switches to the dark steps of the same palette in the Advanced
theme. Do not use fixed hex colours in screens.

## Arabic

Choose **العربية** in My account (or on the sign-in page). The whole interface turns right to left, with
Arabic labels, month and day names; numbers use Western digits, as is usual in the UAE. Dashboards are mirrored.
Names people type (dashboards, charts, fields) are shown as written.

For developers: write English text in `t("…")` and add the Arabic to `frontend/src/lib/i18n/ar.ts`. Use
placeholders for values: `t("Synced {when}", { when })`. Use logical classes (`ms-`, `pe-`, `start-`,
`text-start`, `border-s`) instead of left/right.

## Calendar (Admin → Settings)

- **Show dates as** Gregorian, Hijri (Umm al-Qura) or both. People can choose their own.
- **Week starts on**: used for "week" in charts and for "This week" filters (UAE: Monday).
- **Weekend**: shaded in calendar charts (UAE: Saturday and Sunday).
- **Fiscal year starts in**: adds the date levels *Fiscal year* and *Fiscal quarter* in the chart builder
  (pill menu) and the filters *This fiscal quarter*, *This fiscal year* and *Last 2 fiscal years*. A fiscal
  year is named by the year it ends in: July 2026 – June 2027 is FY2027.

Charts group by Gregorian periods; Hijri is used for how dates are shown.

## Workspaces

A workspace is a shared place for a team's dashboards and charts (for example Operations, Finance).

| Role | Can |
| --- | --- |
| Viewer | Open the dashboards and charts |
| Editor | Also change them and add new ones |
| Manager | Also delete and share them, and manage the members |

- Admins create and delete workspaces. Deleting one keeps its dashboards and charts as personal items of their
  owners.
- Members can be people or groups. People with the Viewer role are always viewers.
- Membership never gives access to data: everyone still sees only the rows their row-level security allows.
- Pick a workspace in the side menu to see only its items; new dashboards and charts go into it.
- Move an item with **More → Move to a workspace** (dashboards) or the folder button (charts).

## Presentation and TV modes

On a dashboard, **Present** shows one section (or tab) per slide, filling the screen. Use the arrow keys,
space or the buttons; Esc leaves.

**TV mode** is for screens on a wall: slides move on by themselves, the data refreshes, and a clock shows when
the data was refreshed. Open the link with options:

```
https://lumen.example.ae/dashboards/12?tv=1&cycle=30&refresh=5
```

- `cycle`: seconds per slide (at least 10, default 30)
- `refresh`: minutes between data refreshes (default 5)

Without signing the screen in, an admin can make a **display link** (More → Display links); see
`docs/EMBEDDING.md`.

## KPI goals, comments, subscriptions and alerts

- **KPI goal**: edit a section (Edit → section settings → KPI goal): the chart whose value is the actual, the
  target, whether higher or lower is better and how far off still counts as "at risk". The section header,
  the navigator and the **Scorecard** show the status, each person with their own data.
- **Comments**: the speech-bubble button. To pin a note to a point of a chart, click the point → *Add a note
  here*.
- **Subscribe** (More → Subscribe): get the dashboard by email (PDF, picture or link) on a schedule. Managers
  can add people and groups and post to Teams; everyone gets their own data.
- **Alerts** (More → Alerts): a message when a chart's value crosses a limit, something unusual happens (the
  latest day, week or month is far from usual) or a KPI goes off track.
- **Earlier versions** (More): the last two versions; Restore goes back (and keeps the current one).
- **Data checks** (Data → dataset → Quality) and **masking** (Security tab) are for dataset owners.

## AI (when an admin has added a model)

- **Ask your data** (Home): type a question in English or Arabic; you get a chart and a short answer from the
  data you may see. *Save chart* or *Open in chart builder* to change it.
- **Chart builder**: *Describe the chart you want* fills the shelves; **Explain** lists what stands out;
  Options → **Forecast** (line and area charts over a date) adds a dashed forecast with its likely range — no
  AI involved.
- **Dashboards**: *Build with AI* makes a starter dashboard for a dataset; a tile's ⋮ menu → *Explain*.
- **Calculated fields**: *Write it for me* turns a description into a formula, checked before you use it.
- **My account**: *Daily summary* (a morning email about your favourite dashboards) and *Connected AI apps*
  (Claude and other apps you allowed to read Lumen).
- Admins: Admin → AI (models, sensitive data, Claude connector, comparison); see `docs/AI.md`.
