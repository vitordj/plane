# Organizational Units (Orca)

An organizational layer for the fork: units (areas, squads, committees) group
workspace members, link to projects with an inherited role, and materialize
native `ProjectMember` rows so Plane's own RBAC stays the single authority for
project access.

Built as relational sidecar tables under a dedicated API namespace, per
[FORK.md](../FORK.md). No core model gains a column.

## The problem

Plane knows that a person belongs to a workspace and to individual projects.
It does not know that a person belongs to Compliance — so onboarding someone
into an area means adding them to each of that area's projects by hand, and
offboarding means remembering all of them.

## The model

```
Workspace
├── OrganizationalUnit ── OrganizationalUnitProject ──> Project
│        │                    (default_role)
│        └── OrganizationalUnitMembership ──> WorkspaceMember
│
├── OrganizationalUnitGrant              (provenance: why access exists)
└── OrganizationalProjectAccessState     (what the layer actually applied)
                    │
                    ▼
              ProjectMember               (native, unchanged)
```

| Table                                  | Purpose                                                                                            |
| -------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `organizational_units`                 | A unit inside a workspace: name, slug, description, `is_active`.                                   |
| `organizational_unit_memberships`      | Person ↔ unit. FK to `WorkspaceMember`, role `lead` or `member`, at most one active lead per unit. |
| `organizational_unit_projects`         | Unit ↔ project, with the `default_role` inherited on that project.                                 |
| `organizational_unit_grants`           | One row per (membership, unit-project) pair that sources access. Revoked rows are kept for audit.  |
| `organizational_project_access_states` | Aggregate per (person, project): `baseline_role`, `last_applied_role`, `created_by_org_layer`.     |
| `issue_organizational_units`           | The unit responsible for a work item.                                                              |

Units and memberships also record **where they came from** — `manual` or
`scim`. Nothing in this document depends on it, but it is what lets an external
directory add and remove its own rows without ever touching one a human
created. See [Directory sync](#directory-sync).

Roles are Plane's native ones throughout: `Admin = 20`, `Member = 15`,
`Guest = 5`.

## Access rules

The inherited role for a person on a project is the **highest** `default_role`
across their active memberships in active units linked to that project, capped
by their workspace role (a workspace Guest never gets more than Guest; a
workspace Admin is never written below Admin — the same guards the native
member API applies).

Two invariants make the layer safe to run repeatedly:

**The inherited role is a floor, and manual access above it wins.** The two
directions are deliberately not symmetric.

_Lowering or withdrawing_ happens only when the current `ProjectMember.role`
still equals `last_applied_role` — the role this layer last wrote. If an admin
promoted someone by hand, the current value is not ours, so the layer
relinquishes its claim and leaves the access alone.

_Raising_ happens freely, because the inherited role is a **floor**. Demoting
someone by hand below what their unit grants does not stick: the next
reconcile puts them back, since the unit still says they belong. To actually
reduce someone's access, remove them from the unit or change the unit-project
link — the reconciler is not something to override row by row. Manual
promotions _above_ the floor are untouched.

**Provenance is explicit.** Grants record every source, so removing one unit
never removes access that another unit — or a manual grant recorded as
`baseline_role` — still justifies. Losing the stronger of two units drops the
person to the weaker unit's role rather than removing them.

Worked example:

| Step                                         | State                                          |
| -------------------------------------------- | ---------------------------------------------- |
| Lucas has manual Guest on Onboarding         | `baseline_role = 5`                            |
| Lucas joins Compliance (Onboarding → Member) | role becomes 15, `last_applied_role = 15`      |
| Lucas leaves Compliance                      | role restored to 5, `last_applied_role = None` |

If instead someone had promoted Lucas to Admin by hand, leaving Compliance
would leave him at Admin: current role (20) ≠ last applied (15), so the layer
does not touch it.

If someone had instead demoted Lucas to Guest by hand _while_ he was still in
Compliance, the next reconcile would put him back at Member. That is the floor
at work, not a bug — Compliance still grants Member on Onboarding.

**The unit lead governs the unit, not its projects.** A lead inherits the same
`default_role` as everyone else. Project Admin is only ever granted explicitly
through the native `ProjectMember` flow, so it stays auditable.

## Reconciliation

Reconciliation is always called explicitly — from the API service layer, the
Celery task, or the management command. There are no Django signals, so the
behavior is predictable and testable.

Mutations affecting up to `ORCA_ORG_SYNC_MAX_EDGES` edges (members × projects,
default 100) reconcile inline inside the request transaction, with
`select_for_update` on the rows being written. Wider fan-outs are queued to
Celery after commit and processed in project batches. Either path is
idempotent: running it twice produces no second set of changes.

To inspect or repair a workspace:

```bash
# Preview (default)
python manage.py reconcile_organizational_access --workspace <slug>

# Write
python manage.py reconcile_organizational_access --workspace <slug> --apply
```

## API

All routes live under `/api/orca/`, matching the namespace the fork's existing
project-state and project-label endpoints use. Mutations require workspace
Admin; reads are open to workspace members.

| Method                 | Path (under `/api/orca/workspaces/<slug>/`)                           |
| ---------------------- | --------------------------------------------------------------------- |
| `GET` `POST`           | `organizational-units/`                                               |
| `GET` `PATCH` `DELETE` | `organizational-units/<id>/`                                          |
| `GET` `POST`           | `organizational-units/<id>/members/`                                  |
| `PATCH` `DELETE`       | `organizational-units/<id>/members/<membership_id>/`                  |
| `GET` `POST`           | `organizational-units/<id>/projects/`                                 |
| `PATCH` `DELETE`       | `organizational-units/<id>/projects/<link_id>/`                       |
| `GET`                  | `organizational-units/<id>/effective-access/`                         |
| `GET`                  | `organizational-units/<id>/workload/`                                 |
| `GET`                  | `organizational-units/me/`                                            |
| `GET` `PATCH`          | `directory/`                                                          |
| `POST` `DELETE`        | `directory/token/`                                                    |
| `POST`                 | `directory/resync/`                                                   |
| `GET`                  | `directory/unresolved/`                                               |
| `GET` `POST` `DELETE`  | `projects/<project_id>/issues/<issue_id>/organizational-unit/`        |
| `POST`                 | `projects/<project_id>/issues/<issue_id>/organizational-unit-assign/` |

`effective-access/` is strictly read-only: it runs the same resolver the
reconciler uses and reports current state, desired state and provenance
without writing anything.

Adding a member takes `workspace_member_ids` and only accepts people who are
already active members of the workspace — a unit never sends invitations.

## Assignment

A unit can be marked responsible for a work item. Because Plane requires an
assignee to be a person who is an active project member, the engine turns that
responsibility into a real assignee: it ranks the unit's members by open work
across the unit's own live projects, least loaded first, breaking ties by
whoever was assigned longest ago and then by user id.

Existing assignees are never replaced. The default mode assigns only when
nobody holds the item; `mode=append` adds a unit member alongside the current
ones. Triggering is manual in v1.

## Directory sync

Microsoft Entra ID can supply unit membership over SCIM 2.0, so onboarding
someone into an area becomes a directory change rather than a Plane one. Two
rules keep it safe to point at a workspace already in use:

- the sync only withdraws memberships it created itself, so a person an admin
  added by hand survives a group change upstream — the same shape as the
  "manual access always wins" rule above, one layer up;
- a unit still never invites anyone. People the directory pushes who are not
  workspace members are recorded and reported, and become members of their
  units by themselves once they join.

What a unit _grants_ — its projects and their inherited roles — stays a Plane
decision that no SCIM call can reach. Setup, endpoints and troubleshooting are
in [entra-directory-sync.md](./entra-directory-sync.md).

## Settings

| Setting                   | Default | Effect                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ORCA_ORG_UNITS_ENABLED`  | `1`     | Kill switch. Accepts `1/true/yes/on` and `0/false/no/off` (any other value refuses to start). Set to `0` and every `/api/orca/` organizational-unit route answers 404 — the directory connection endpoints and the SCIM provisioning endpoints included — both management commands refuse to run, the hourly directory pass and any queued reconciliation task return without writing, and the UI hides the layer. The switch is read where the write would happen, so a task already on the queue when it is flipped does not land afterwards. Existing inherited `ProjectMember` rows are left exactly as they are — the switch stops the layer acting, it does not withdraw access it already granted. Re-enable and reconcile to resume. |
| `ORCA_ORG_SYNC_MAX_EDGES` | `100`   | Fan-out threshold for inline vs. Celery reconciliation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

Directory provisioning is configured per workspace, not per instance — a
workspace admin issues the SCIM token from **Workspace settings → Areas**.

The interface language new members start in is an instance setting rather than
an organizational-unit one; see [i18n.md](./i18n.md).

## Tests

```bash
cd apps/api
pytest plane/tests/unit/orca/
```

The directory layer has its own files —
`test_scim_endpoints.py`, `test_directory_projector.py`,
`test_directory_admin_api.py` and `test_entra_provider.py`.

They cover joining and leaving units, the strongest-role resolution across two
units, manual access surviving removal, manual promotions never being
reverted, manual demotions below the inherited floor being restored,
workspace-role capping, idempotency, the read-only guarantee of `plan_access`,
cross-workspace rejection, and the assignment engine's ranking and
no-replacement rules.

They also cover the hardening invariants: that the reconciliation task is
registered on worker startup, that a responsible unit can be set, cleared and
set again, that `workspace_member` and `project` cannot be re-pointed by
PATCH, that role and lead rules are validated when adding members, that
workspace label and state writes are Admin-only, and that the kill switch
closes the API.
