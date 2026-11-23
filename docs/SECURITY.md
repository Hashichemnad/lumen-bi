# Data security: who sees which data

Two layers, set per dataset on its **Security** tab (owner or admin):

1. **Who can use the dataset.** Creators and admins can always use every dataset. Viewers and explorers
   need access: everyone, chosen people, or groups. (Milestone 9 adds access through shared dashboards.)
2. **Row-level security (RLS).** Rules that limit *which rows* each person sees. They apply to everyone
   except admins, Creators included, wherever data is read: charts, tables, totals, % of total, top N,
   filter lists, exports, previews, formula samples and (later) AI answers and email reports.

## Rules

**User attribute.** "Only show rows where *Region* is one of the user's *region* values." Set attributes
on each user (Admin → Users, e.g. `region = UAE, KSA`), import them from CSV, or copy them from Microsoft
Entra / UAE PASS claims on sign-in (Admin → Sign-in methods). Built-in attributes: `email`, `name`,
`user_id`, `groups` (group names).

**Access table.** For access lists that live in another system: sync a dataset that maps users to values
(e.g. Salesforce territory assignments, an Oracle table, an Excel sheet with `email, branch`) and choose
it as the access table. Choose the column with the user, the user attribute it is matched with (email by
default), the column with allowed values, and optionally a value meaning "all rows" (e.g. `*`).

Options on every rule:

- **Ignore case** (on by default): `uae`, `UAE ` and `UAE` match.
- **Groups that see all rows**, e.g. Management.
- **On/off**, to test or pause a rule.

The rule's field can be a column or a row-level calculated field (e.g. `UPPER([Region])`).

## How rules combine

- All rules on a dataset apply together (AND): Region = user's region *and* Product line = user's lines.
- **Fail closed.** If a person has no value for the attribute, is not in the access table, the access table
  has not synced, or the rule's field no longer exists, the rule shows **no rows**, never all rows. Fix the
  attribute or rule and access returns at once (no cache to wait for).
- **Joins and unions inherit rules.** A dataset built from others carries their rules, mapped to its own
  columns (`Customers.region` for a joined Customers dataset). Building a join is never a way around a rule,
  even for its owner. If a rule's field is not part of the joined dataset, it blocks every row there.
- A field used by a rule cannot be deleted; renaming it updates the rule. An access table cannot be
  deleted while a rule uses it.

## Checking

**Check what someone sees** on the Security tab: pick a person to see whether they can use the dataset,
which rules apply to them with which values (or why a rule blocks them), how many rows they see, and their
first rows. Each check is recorded in the audit log, as are rule and access changes and exports.

## How it is enforced (for reviewers)

- Server only, in the query engine (`backend/app/query/`). The browser never receives rows it may not see.
- The dataset's data is read in exactly one place in every generated query, the `base` CTE, which carries
  the RLS condition. Filters, top-N subqueries, totals, table calculations and value lists all read `base`.
- The user's values are bound parameters (`$1`, `$2`…), never pasted into SQL; fields go through the
  semantic layer and formula compiler (identifiers quoted, literals escaped).
- The result cache key is a hash of the final SQL, its bound values and the snapshot files read, so people
  with different access never share a cached result, and a sync is never served stale results.
- Tests: `backend/tests/test_rls.py` (users seeing only their rows through every read path, fail-closed
  cases, inheritance through joins/unions, cache isolation, permissions, injection attempts).
