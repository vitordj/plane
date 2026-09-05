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

## Auditing the routing

Two rules about assigned work cannot be database constraints, because they
span tables a `CHECK` cannot see: an item a unit considers assigned must have
its executor as a live assignee, and that executor must still be an active
member of the unit and of the project. Ordinary Plane operations break them —
removing an assignee in the work item, taking someone off a project, a
directory sync withdrawing a membership — so they are checked on demand:

```bash
# Report (default)
python manage.py audit_organizational_routing --workspace <slug>

# Repair
python manage.py audit_organizational_routing --workspace <slug> --write
```

`--write` returns the affected items to the unit's queue with the reason
`executor_unavailable`, through the same service any other allocation goes
through, so the repair is a recorded decision rather than an untraceable
update. The assignee row itself is left alone: detaching a person is a human's
call.

Two findings are reported and never repaired — a queued item that already has
an assignee (it may be a collaborator a coordinator added on purpose) and a
policy whose default mode is outside its own allowed list.

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
| `GET`                  | `organizational-units/<id>/policy/`                                   |
| `GET`                  | `organizational-units/<id>/projects/<project_id>/policy/`             |
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

A unit can be marked responsible for a work item **in a project the unit
covers** — one it is linked to, where the link is live and the project is not
archived. Coverage is what grants the unit's members access in the first
place, so an uncovered project would name a group that cannot act there: the
API refuses it with `ORG_UNIT_NOT_COVERING_PROJECT` (4916), the ranking offers
no candidate, and the picker in the work item only lists units that cover the
project it belongs to. The unit payload carries `project_ids` for exactly that
filter, with archived projects left out.

Plane requires an assignee to be a person who is an active project member, so
a unit being responsible is not the same as the work being handed out. The
layer keeps both facts, side by side:

| Field                | Meaning                                                                              |
| -------------------- | ------------------------------------------------------------------------------------ |
| `organizational_unit`| The unit answerable for the item.                                                    |
| `routing_state`      | `queued`, `assigned`, `allocation_failed` or `suspended`.                            |
| `queue_reason`       | Why it is waiting: awaiting a coordinator, awaiting a claim, nobody eligible, …      |
| `primary_executor`   | The person answerable for it. Set only while `assigned`, enforced by a `CHECK`.      |
| `assignment_due_at`  | When the assignment SLA runs out, if the policy sets one.                            |

The primary executor is always also a plain `IssueAssignee`, so the item looks
normal everywhere in Plane. The reverse does not hold: other assignees are
collaborators, and they do not count toward anybody's load.

### Policies

**What happens when a unit is made responsible** is the unit's *assignment
policy*:

| Mode           | Effect                                                           |
| -------------- | ---------------------------------------------------------------- |
| `manual`       | A coordinator will decide, so the item waits.                    |
| `self_claim`   | It waits for someone in the unit to take it.                     |
| `least_loaded` | It is handed to the least loaded eligible member on the spot.    |

A policy can be set for the unit or for one of its projects, and the
project's wins. A unit with no policy defaults to `manual` — nothing is handed
out by itself — but permits any mode a caller asks for; once a policy sets
`allowed_modes`, asking for a mode outside that list is refused with
`ORG_ASSIGNMENT_MODE_NOT_ALLOWED` (4917) rather than quietly downgraded, since
a caller told "assigned" about an item sitting in a queue nobody is watching
is worse off than one told "no". `GET .../policy/` reports the resolved
policy without writing anything, which is how the interface knows whether to
offer "assign automatically" at all.

`least_loaded` ranks the unit's members by open work — items where they are
the primary executor and the state group is neither completed nor cancelled —
then by open work in this unit, then by whoever went longest without an
automatic assignment, then by user id, so two runs over the same data agree.
People who are not assignable project members, bots, and anyone over the
policy's `max_open_items_per_member` are excluded, and the decision records
why. Concurrent allocations in one unit serialize on an advisory lock, so
twenty items handed out at once spread evenly instead of piling onto whoever
was least loaded when the first request arrived.

### The record

Every allocation writes an `AssignmentDecision`: the mode that governed it,
where that mode came from, the ranking algorithm's version, everyone it
considered with their load, who was chosen, who held it before, and how it
ended. Decisions are append-only — a change writes a new one pointing at the
one it supersedes — so "why does this person have this?" is answerable a week
later. Changes of responsible unit write an `IssueResponsibilityEvent`,
including clearing the unit, which deletes the link but keeps the history and
leaves the assignees alone: the item goes back to being an ordinary Plane
work item.

### Triggering it

Assignment is manual in v1: `POST .../organizational-unit-assign/` is the
button. It asks for `least_loaded` regardless of the unit's default, because
a person pressing "assign to whoever has least open work" is not the unit's
policy acting on its own; a unit that excluded that mode refuses. Existing
assignees are never replaced — the default leaves an item that already has one
alone, and `mode=append` adds a unit member alongside the current ones.
`mode` is the older vocabulary and is deprecated: it says what to do about
people already on the item, not how to choose one, and `assignment_mode` is
the field that names a mode.

### What changed from v1

The engine used to add the project to the unit's own list when it was
missing, which turned "not covered" into "covered" and made work in a project
the unit does not own count toward its members' load. Coverage is a
precondition now. Load counts only the primary executor, so a collaborator
left over from an earlier assignment is no longer pushed down the ranking for
work they do not own. And an assignment made through the route leaves a
responsibility link, a routing state and a decision, where before it wrote an
assignee and nothing else.

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
`test_directory_admin_api.py` and `test_entra_provider.py` — and so does
assignment: `test_assignment_service.py` (policy resolution, ranking, the four
allocation paths, claim, reassign, transfer), `test_routing_transitions.py`
(the state machine), `test_assignment_models.py` (append-only decisions,
policy constraints), `test_assignment_concurrency.py`,
`test_assignment_metrics.py` and `test_audit_routing_command.py`.

They cover joining and leaving units, the strongest-role resolution across two
units, manual access surviving removal, manual promotions never being
reverted, manual demotions below the inherited floor being restored,
workspace-role capping, idempotency, the read-only guarantee of `plan_access`,
cross-workspace rejection, archiving a project withdrawing what the unit
granted, and the ranking's own rules — least loaded first, collaborators not
charged, finished work not counted, existing assignees never replaced.

The concurrency file needs real transactions and threads; the pattern is in
[apps/api/tests/RUNNING_TESTS.md](../apps/api/tests/RUNNING_TESTS.md).

They also cover the hardening invariants: that the reconciliation task is
registered on worker startup, that a responsible unit can be set, cleared and
set again, that `workspace_member` and `project` cannot be re-pointed by
PATCH, that role and lead rules are validated when adding members, that
workspace label and state writes are Admin-only, and that the kill switch
closes the API.
