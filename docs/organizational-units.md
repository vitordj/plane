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

An area can be marked responsible for a work item. Because Plane assigns work
to people — an assignee has to be an active project member — that
responsibility has to become a person, and that is what the assignment service
does. Everything that assigns goes through it: the app, the management
commands, and (from Phase 1) the public API.

**An area only owns work in the projects it covers.** Marking an area
responsible for a work item in a project it is not linked to is refused: its
members inherit project access from that link, so work routed anywhere else
either has nobody eligible or lands on somebody who cannot see it.

### Queue state

Marking an area responsible and somebody executing the work are two different
facts, and the work item carries both:

| State | What it means |
| --- | --- |
| `queued` | The area owns it; nobody is executing it yet. `queue_reason` says whether it is waiting for a coordinator, waiting to be claimed, or newly arrived. |
| `assigned` | Somebody is the **primary executor** — the one person answerable — and is a native assignee. |
| `allocation_failed` | Automatic allocation ran and found nobody eligible. Different from `queued`: it usually means the area's membership or its project links are wrong, not that everybody is busy. |
| `suspended` | A coordinator parked it. It is in nobody's queue until they resume it. |

Other people can be on the work item as native assignees — collaborators. They
carry no responsibility and no load: only the primary executor does.

### Policies

An area has one default policy, and may override it per project. Two levels,
deliberately: enough to say "this area works manually, except in the onboarding
project, where whoever is free takes it", and no more, because a third level is
a rule nobody can predict the result of.

| Mode | Who ends up with the work |
| --- | --- |
| `manual` | Nobody, until a coordinator assigns it. The default when an area has no policy at all. |
| `self_claim` | Any eligible member of the area may take it. |
| `least_loaded` | The service picks whoever currently carries the least open work. |
| `explicit` | The caller named the person. Still checked for eligibility. |

`allowed_modes` is what a caller may ask for. A request outside it is
**refused, never quietly downgraded** — an automation that asked for automatic
allocation and silently got a manual queue looks like it worked, and the work
sits there.

Automatic allocation counts open work as primary executions, workspace-wide
first and then within the area, breaking ties by whoever was picked
automatically longest ago and finally by user id, so the same inputs always
produce the same choice. Concurrent automatic allocations in one area
serialize, so two requests cannot read the same load and hand the same person
both work items.

### Decisions

Every change of executor or queue state writes an `AssignmentDecision`: the
policy that applied, its version, the ranking that was in front of the service
(ids only, no names), who decided, and what it replaced. The question a
coordinator asks about an automatic choice is "why them and not me?", and this
is what answers it.

Reassigning **keeps the previous executor on the work item** as a collaborator
— they have context somebody will want — and so does returning work to the
queue. Clearing the area removes the link and records the event, and leaves
the native assignees alone: the work item goes back to being an ordinary Plane
work item, with the same people on it.

### Auditing

Nothing outside the service maintains those invariants, and things drift:
somebody clears an assignee in the app, a person leaves a project, an area's
project links change. The audit reports it:

```bash
# Report (default; writes nothing)
python manage.py audit_organizational_routing --workspace <slug>

# Return work with an unusable executor to the queue
python manage.py audit_organizational_routing --workspace <slug> --write
```

It checks four things: an executor who is no longer an assignee, an executor
who is no longer able to do the work, something queued that somebody is
already assigned to, and a policy whose default mode is not in its own allowed
list. `--write` repairs only the first two — the third may be a collaborator
somebody added on purpose, and taking that away would be worse than the drift.
Worth running daily in report mode.

### What changed from v1

The first version created a native assignee and nothing else: no queue state,
no record of why that person, and no way to tell "nobody was available" from
"nobody has looked at it yet". Its `mode=fill_empty` and `mode=append` are
still accepted by the assign endpoint and both now mean automatic allocation —
`append` is what the service always does, since it never replaces an existing
assignee. They are deprecated and go away in Phase 2.

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

| Setting                   | Default | Effect                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ORCA_ORG_UNITS_ENABLED`  | `1`     | Kill switch. Set to `0` and every `/api/orca/` organizational-unit route answers 404 — the directory connection endpoints and the SCIM provisioning endpoints included — the reconcile management command refuses to run, and the UI hides the layer. Existing inherited `ProjectMember` rows are left exactly as they are — the switch stops the layer acting, it does not withdraw access it already granted. Re-enable and reconcile to resume. |
| `ORCA_ORG_SYNC_MAX_EDGES` | `100`   | Fan-out threshold for inline vs. Celery reconciliation.                                                                                                                                                                                                                                                                                                                                                                                            |

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
