# Plane Compose, and why it is not how instances are created

**Decision (RFC F12, confirmed): Compose defines *schema*. Instances always go
through the public API.** Nothing found while checking contradicts that, and
two facts about this fork make it stronger than the RFC assumed.

## What Compose is

Plane Compose is Plane's "projects-as-code" tool: a `plane.yaml` naming the
workspace and project plus schema and work files, edited locally and pushed
with `plane push` (or `plane schema push` for schema alone). Local files are
the source of truth; the Plane instance is the target. `plane pull` writes the
current remote state back down into files.

That direction of travel is the whole argument. A process instance is created
by something that happened outside Plane — a person hired, a ticket raised —
and lives for a week. Making Compose the engine would mean the orchestrator
generating YAML, committing it, and pushing, so that a tool whose contract is
"the files are the truth" would be fed files that are themselves generated
from an event stream nobody can replay from Git. The API is the right shape
for that, and the fork already has one (`/api/v1/orca/`, with idempotency and
external bindings — see [`orca-public-api.md`](./orca-public-api.md)).

## Two things about this fork that settle it

**An area cannot be a Compose field.** Plane CE 1.4.x has no custom-property
model at all (`plane/db/models/` has `issue_type.py` but nothing for
properties; custom properties are a paid-tier feature). Which area is
responsible for a work item lives in `IssueOrganizationalUnit`, a sidecar
table, and only `/api/orca/` and `/api/v1/orca/` write it. So even a
Compose-declared work item would arrive with no area, no queue state and no
executor — exactly the state the queue exists to prevent.

**Work item types exist but are not managed anywhere in CE.** `IssueType` and
`ProjectIssueType` are real models and the v1 API accepts a `type_id`, but CE
ships no endpoint that creates or lists them. If a Compose schema file is
expected to declare types, that is a gap to find out about before relying on
it, not after.

## What Compose is still worth having

Project schema that should be reviewable and repeatable: states, labels,
project structure. The fork already treats two of those as first-class
(`plane/db/models/project_state.py`, `project_label.py`, with workspace-level
settings), so a schema file per project is a natural fit and would make a new
area's projects reproducible instead of hand-built.

## Still to confirm before relying on it

The official documentation could not be read from the environment this note
was written in — `developers.plane.so` and `plane.so` are both blocked by the
network egress proxy — so these are open, and each one is a question for
whoever runs the first `plane push`:

1. **Authentication.** Which credential `plane push` uses, and whether it is a
   personal API key (in which case pushes are attributed to a person) or
   something workspace-scoped.
2. **Re-push with the same id.** Whether pushing an unchanged work file twice
   is a no-op, an update, or a second work item. This is what decides whether
   Compose can be run from CI at all.
3. **State or lock file.** Whether Compose keeps a local record mapping files
   to remote ids, and if so whether it belongs in Git (shared identity) or is
   per-checkout (and pushing from two machines diverges).
4. **CE vs Pro.** Whether `plane push` refuses schema features the CE build
   does not have — work item types in particular.
5. **Whether it touches work items the fork owns.** A `plane pull` of a project
   an area covers would write down work items whose responsibility lives in a
   sidecar table; a later `push` must not be able to recreate or reword them.

Until 1–3 are answered, use Compose by hand on a scratch project, not from CI,
and never against a project an area covers.
