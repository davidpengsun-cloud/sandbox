# Portal access

The portal uses KGE's sign-in and username. It displays `name` from KGE's `/whoami`. KGE access admins (`/whoami.admin`) always have portal admin access, alongside the portal's own admin grants. A pack role named `admin` does not confer portal administration.

This installation bootstrap requires connected KGE. The separate standalone credential-file mode does not supply KGE's access-admin flag and does not acquire an administrator from a username.

In **Admin → User Management**, an admin can list, grant and remove portal roles and read their audit. Viewer is the default. Publisher retains publishing and metadata editing; approver retains request decisions; admin retains both plus role management. All product operations remain restricted to the person's readable units. A portal grant changes authorisation on the next request. KGE membership and its admin flag follow KGE's live access refresh.

The server exposes:

| Operation | Route |
|---|---|
| List explicit portal grants and available roles | `GET /api/admin/users` |
| Grant one role | `PUT /api/admin/users/:username/roles/:role` |
| Remove one role | `DELETE /api/admin/users/:username/roles/:role` |
| Read role audit | `GET /api/admin/users/audit` |

Writes take no body. The signed-in identity supplies the actor. Unknown roles and non-admin callers are refused. Removing the portal's admin grant cannot remove KGE access-admin authority. During a KGE outage, the existing catalogue fallback does not authorise privileged operations.

SQLite and BigQuery store explicit grants in `_portal_users` and audit changes in `_portal_role_audit`, including actor, time, target and before/after grants. Existing installed grants survive the schema upgrade, except the three grants earlier versions seeded by name: `admin` as admin, `steward` as approver and `analyst_retail` as viewer. On its first start, the upgraded portal removes exactly those pairs and writes one audit entry for each, with actor `upgrade`. The removal runs once, recorded in `_portal_upgrades`, so a grant you make later to any of those names stays. Fresh installations seed no roles by name.

## Usernames

At sign-in, the portal passes the username to KGE exactly as typed, and KGE decides. The portal normalises the typed name only to count failed attempts, so case and look-alike variants of one name share one throttle. After sign-in, the `id` from KGE's `/whoami` is the person's key in every portal table, as KGE gives it.

The portal normalises the names an operator types: the username in a grant or removal, and the new usernames in a migration map. Normalisation strips the name, applies NFKC, casefolds it and applies NFKC again. A name is refused if it is empty, longer than 254 characters, contains whitespace or a control or format character, is `access_admin`, or starts with `role:`. `demos/kge/portal/server/test/username-vectors.json` lists inputs with their normalised form, or `null` where the name is refused. The portal's tests check against this file. KGE's own username normalisation should be tested against the same file, so that a name an operator grants matches the `id` KGE reports.

## Identity migration

Use the same mapping supplied to KGE's migration, including any legacy names requiring normalisation. The JSON object maps each exact old principal ID to its new username:

```json
{"old_reader": "reader@example.com", "old_reviewer": "reviewer@example.com"}
```

Run once during the coordinated KGE/portal identity change, with portal writes stopped, from `portal/server` using its usual repository settings:

```sh
node --experimental-sqlite src/migrate-identities.js principal-to-username.json operator@example.com
```

This uses the configured repository factory; it does not call KGE or guess a mapping. The operation migrates all seven identity-bearing tables, including request decisions, and existing role-audit identities. It unions favourites, cart entries and role memberships at an existing destination; all event rows and their IDs survive. The migration ledger records the original ID, destination, operator and time. Repeating the same map is harmless; conflicting destinations and ambiguous chains/cycles are refused atomically.

New catalogue titles replace unchanged previous default owners when KGE refreshes. Explicit portal edits remain portal-owned.

A card's descriptive fields change through `catalogue.yaml` and a release: when KGE refreshes, a name, description, domain, community, application, product type, portal-owned badge, or field business name or description that `catalogue.yaml` changed replaces the card's value. A portal admin's or publisher's edit through the metadata API stays until `catalogue.yaml` changes that field again; an edit made since the last refresh also holds against that refresh's change. Governed badges (`CERTIFIED`, `GOVERNED`, `RESTRICTED`, `PII`) remain KGE's. A product that leaves the release is hidden, not deleted: its favourites, cart entries, requests, feedback and portal record are kept and return with it.
