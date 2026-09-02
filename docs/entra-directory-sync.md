# Microsoft Entra ID (Azure AD)

Two independent integrations, either of which can be turned on alone:

|                           | What it decides                                        | Where it is configured                         |
| ------------------------- | ------------------------------------------------------ | ---------------------------------------------- |
| **Sign-in (SSO)**         | Who may log in to Plane                                | God Mode → Authentication → Microsoft Entra ID |
| **Directory sync (SCIM)** | Who belongs to which [area](./organizational-units.md) | Workspace settings → Areas → Directory sync    |

They meet only at the email address: a person who signs in with their corporate
account lands in the areas their Entra groups put them in, because both sides
identify the same human by the same mailbox.

---

## Part 1 — Sign-in with Entra ID

### In the Entra admin center

1. **Identity → Applications → App registrations → New registration.**
   - Supported account types: **Accounts in this organizational directory only.**
     This matters — see [Why the tenant is pinned](#why-the-tenant-is-pinned).
   - Redirect URI: platform **Web**, value `https://<your-plane-host>/auth/entra/callback/`.
2. From **Overview**, copy the **Application (client) ID** and the
   **Directory (tenant) ID**.
3. **Certificates & secrets → New client secret.** Copy the secret **Value**
   (not the Secret ID) — Entra shows it once, and it expires on the date you pick.
   Put that date in a calendar: sign-in breaks the day it lapses.
4. **API permissions →** confirm the delegated Microsoft Graph permission
   **`User.Read`** is present. It is granted by default on a new registration,
   and it is what lets Plane read the signed-in person's name and email.

### In Plane

God Mode (`/god-mode`) → **Authentication → Microsoft Entra ID**: paste the
tenant id, client id and client secret, save, and switch the method on. A
**Sign in with Microsoft** button appears on the login screen.

### Why the tenant is pinned

Plane matches an OAuth identity to an account **by email**. A provider that can
assert an arbitrary email can therefore take over an existing account — the
class of issue behind [GHSA-7j95-vh8g-f365](https://github.com/makeplane/plane/security/advisories/GHSA-7j95-vh8g-f365),
which is why the Google and Gitea providers refuse unverified addresses.

Microsoft guarantees that a signed-in user belongs to _some_ tenant, not to
yours. Pointing this provider at the multi-tenant `common` or `organizations`
authority would let anyone create their own free Azure tenant, put a user in it
whose `mail` is `ceo@yourcompany.com`, and sign in as them. So:

- a specific tenant id is **required**, and `common`, `organizations` and
  `consumers` are rejected outright;
- every returned token's `tid` claim is checked against it, and a mismatch
  fails the sign-in;
- guest accounts are refused, because a guest's UPN
  (`someone_other.com#EXT#@tenant.onmicrosoft.com`) is an internal identifier
  rather than a mailbox. Guests who need Plane should get a mailbox in your
  tenant, or sign in another way.

To federate several tenants, run a separate registration per tenant rather than
relaxing this.

---

## Part 2 — Directory sync (SCIM 2.0)

### What it does, and what it deliberately does not

An Entra group binds to an area. Entra supplies **who belongs**; Plane keeps
deciding **what that means** — which projects the area grants and at which role
are set in Plane and no SCIM call can touch them. A freshly provisioned group
therefore grants nothing until somebody links it to projects, which is the
intended safe default.

Access flows in three steps, each independently inspectable:

```
Entra  ──SCIM──▶  directory mirror  ──projector──▶  area membership  ──reconciler──▶  ProjectMember
                                                                                      (native, unchanged)
```

Two rules govern every write, and they are the reason it is safe to point a
live directory at a workspace people already use:

- **A sync only takes back what the sync gave.** Memberships the directory
  created are marked as such and are the only ones it can withdraw. Somebody an
  admin added by hand keeps their area — and their access — even when they are
  absent from the group upstream. The same rule applies one layer down: the
  reconciler never lowers a `ProjectMember` role that no longer matches what it
  last wrote, so a manual promotion is never reverted.
- **An area never invites anyone.** Entra will happily push people who have no
  Plane account or are not members of this workspace. They are recorded and
  reported, not provisioned — no account is created and no seat is consumed.
  Invite them to the workspace and their area memberships appear on the next
  sync, with nothing to re-push from Entra.

### Set it up

**In Plane** — Workspace settings → **Areas → Directory sync** (workspace admins
only):

1. **Issue token.** Copy it immediately; only its digest is stored and it can
   never be read back.
2. Copy the **Tenant URL** shown next to it. It looks like
   `https://<your-plane-host>/api/orca/scim/v2/workspaces/<workspace-slug>`.
3. Switch **Directory sync** on.

**In the Entra admin center** — Identity → Applications → **Enterprise
applications → New application → Create your own application** (non-gallery):

4. **Provisioning → Get started → Mode: Automatic.**
   - Tenant URL: the value from step 2.
   - Secret Token: the token from step 1.
   - **Test Connection**, then Save.
5. **Mappings → Provision Microsoft Entra ID Users.** Make sure `userName` and
   `mail` map to attributes that hold the person's real mailbox — that address
   is the join key to their Plane account. Everything else is optional; Plane
   ignores attributes it has no column for rather than failing the record.
6. **Users and groups → Add assignment.** Only assigned groups are provisioned,
   so this is where you choose which groups become areas. Start with one.
7. **Provisioning → Start provisioning.** The first cycle can take up to 40
   minutes; Entra syncs roughly every 40 minutes afterwards.

### Then, in Plane

- Each provisioned group appears under **Areas**, badged **Synced**.
- **Link its projects and set the inherited role.** Until you do, the area
  grants nothing.
- Read **Pushed by the directory, not in this workspace**. Those are the people
  Entra sent who are not workspace members. Invite them, or take them out of the
  group upstream.

### The switches

| Setting                             | Default | What off means                                                                                                                                                                                                |
| ----------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Create areas for new groups**     | On      | The directory may only fill areas you created and named to match. Use this when you want to curate which groups become areas.                                                                                 |
| **Let the directory remove people** | On      | Sync becomes additive only — useful while directory data is still being cleaned up, so a half-populated group cannot strip people of access on its first run. Manually added people are protected either way. |

Both switches live on the workspace's connection. Above them sits the
instance-wide kill switch for the whole organizational layer,
`ORCA_ORG_UNITS_ENABLED` (see `organizational-units.md`): set to `0`, the
directory endpoints on this screen answer 404 and every SCIM call — discovery
included — gets a 404 error envelope, so Entra's _Test Connection_ fails until
the layer is switched back on. Nothing is deleted; provisioning simply cannot
write while the layer is off.

### Rotating and revoking

**Rotate token** replaces the credential immediately, so provisioning fails
until Entra is given the new one — do the two together. **Revoke** removes the
credential and switches provisioning off; areas and memberships are left exactly
as they are, simply frozen.

### Day-to-day behaviour

| What happens in Entra                  | What happens in Plane                                                                                                             |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Person added to a provisioned group    | Joins the area, gains its project access                                                                                          |
| Person removed from the group          | Leaves the area, loses the access the area granted — unless an admin added them by hand, or another area still grants it          |
| Person deprovisioned (`active: false`) | Same as being removed, from every area                                                                                            |
| Group renamed                          | Area renamed                                                                                                                      |
| Group deleted or unassigned            | Area is **unbound, not deleted** — it keeps its projects and its manual members; only directory-granted memberships are withdrawn |
| Person not in the workspace            | Nothing. They appear in the unresolved report                                                                                     |

Deleting a synced area in Plane is temporary: the next cycle recreates it.
Remove the group in Entra instead.

---

## Troubleshooting

**Test Connection fails in Entra.** Nearly always the token or the URL. Check
from a shell:

```bash
curl -i -H "Authorization: Bearer <token>" \
  https://<your-plane-host>/api/orca/scim/v2/workspaces/<slug>/ServiceProviderConfig
```

A `401` means the token is wrong, the connection is switched off, or the slug
belongs to another workspace — all three answer alike on purpose, so that an
unauthenticated caller cannot use the response to discover which workspaces
exist. Anything other than `200` or `401` is a routing or proxy problem: the
SCIM paths are capitalized and carry **no trailing slash**, and a proxy that
rewrites either will break provisioning.

**Entra provisioned people but they got no access.** Check, in order: the area
has projects linked; the people are in the unresolved report (then they are not
workspace members); the connection is switched on.

**Somebody keeps reappearing in an area after you remove them.** They are in
the Entra group. Remove them there — a membership deleted in Plane comes back on
the next cycle.

**Somebody has access you cannot find the source of.** Run the read-only
resolver, which reports current state, desired state and provenance without
writing:

```
GET /api/orca/workspaces/<slug>/organizational-units/<unit-id>/effective-access/
```

### From the command line

```bash
# What the directory mirror would produce, and who it could not resolve
python manage.py sync_organizational_directory --workspace <slug>
python manage.py sync_organizational_directory --workspace <slug> --report-only

# Rebuild project access from the areas (preview by default)
python manage.py reconcile_organizational_access --workspace <slug>
python manage.py reconcile_organizational_access --workspace <slug> --apply
```

An hourly background task runs the same projection for every workspace with an
enabled connection. It is what turns a parked identity into real access once
that person joins the workspace — the one event SCIM cannot tell us about.

---

## Reference

### SCIM endpoints

Base: `/api/orca/scim/v2/workspaces/<slug>/`

| Resource                                            | Methods                             |
| --------------------------------------------------- | ----------------------------------- |
| `ServiceProviderConfig`, `ResourceTypes`, `Schemas` | `GET`                               |
| `Users`, `Users/<id>`                               | `GET` `POST` `PUT` `PATCH` `DELETE` |
| `Groups`, `Groups/<id>`                             | `GET` `POST` `PUT` `PATCH` `DELETE` |

Filtering supports the single `attribute eq "value"` term Entra emits, on
`userName`, `externalId`, `id` and `emails` for users and on `displayName`,
`externalId` and `id` for groups. Any other expression is rejected as
`invalidFilter` rather than silently ignored, since ignoring a filter returns
the wrong resources rather than fewer. Pagination is SCIM's 1-based
`startIndex`/`count`, capped at 500.

### Administration endpoints

Workspace admin only, under `/api/orca/workspaces/<slug>/`:

| Method          | Path                    | Purpose                                   |
| --------------- | ----------------------- | ----------------------------------------- |
| `GET` `PATCH`   | `directory/`            | Read and configure the connection         |
| `POST` `DELETE` | `directory/token/`      | Issue (returns the token once) and revoke |
| `POST`          | `directory/resync/`     | Replay the mirror; calls nothing external |
| `GET`           | `directory/unresolved/` | The identities that granted nothing       |

### Instance configuration keys

Set through God Mode rather than the environment:

| Key                   | Notes                                                       |
| --------------------- | ----------------------------------------------------------- |
| `IS_ENTRA_ENABLED`    | `1` shows the sign-in button                                |
| `ENTRA_TENANT_ID`     | Required; `common`/`organizations`/`consumers` are rejected |
| `ENTRA_CLIENT_ID`     | Application (client) ID                                     |
| `ENTRA_CLIENT_SECRET` | Stored encrypted                                            |
| `ENABLE_ENTRA_SYNC`   | Whether sign-in refreshes the Plane profile from Entra      |

### Tables

All sidecars, per [FORK.md](../FORK.md) — no core table is touched.

| Table                                        | Purpose                                                             |
| -------------------------------------------- | ------------------------------------------------------------------- |
| `organizational_directory_connections`       | Per-workspace config and the token digest                           |
| `organizational_directory_identities`        | One SCIM `User` as pushed, plus the workspace member it resolves to |
| `organizational_directory_group_memberships` | The directory's assertion that an identity is in a group            |

The mirror is kept separate from `organizational_unit_memberships` on purpose:
it holds every member of a group, resolvable or not, so a person who leaves and
rejoins the workspace regains their areas without Entra pushing anything again.

### Tests

```bash
cd apps/api
pytest plane/tests/unit/orca/test_scim_endpoints.py
pytest plane/tests/unit/orca/test_directory_projector.py
pytest plane/tests/unit/orca/test_directory_admin_api.py
pytest plane/tests/unit/orca/test_entra_provider.py
```
