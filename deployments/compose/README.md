# Plain Compose on a VM

The deployment target for 4UM is a plain `docker compose` stack on an internal
VM: no PaaS, no ingress controller, no orchestrator. Caddy — the `proxy`
service of [`docker-compose-orca.yml`](../../docker-compose-orca.yml) — is the
edge.

What lives here is the part a PaaS would otherwise have supplied: an update
procedure that verifies what it deployed, and an override that says what a host
with nothing in front of it has to set.

| File | What it is |
| --- | --- |
| [`update.sh`](./update.sh) | Pull, migrate, up, verify. Section 4 of [the release runbook](../../docs/release-runbook.md) made executable. |
| [`compose.override.example.yaml`](./compose.override.example.yaml) | Publishing port 80, `TRUSTED_PROXIES` for a stack that is its own edge, and resource limits for a host with more than 3 GB. |

## The shape of a stack directory

```text
/opt/stacks/<name>/
├── compose.yaml                # verbatim copy of docker-compose-orca.yml
├── compose.override.yaml       # everything specific to this host
├── .env                        # secrets and TAG, mode 0600, never committed
└── update.sh                   # a copy of this directory's update.sh
```

`docker compose` merges `compose.yaml` and `compose.override.yaml` by name, so
nothing needs `-f`.

**What has to be in `.env`, not in the override.** Compose interpolates each
file on its own *before* merging them, so a variable the base file declares
required -- `${TRUSTED_PROXIES:?...}` -- is not satisfied by a literal in the
override. `docker compose config` fails with `required variable
TRUSTED_PROXIES is missing a value` and nothing starts. The minimum `.env` for
a stack that is its own edge:

```dotenv
TAG=sha-<commit>
DOMAIN_NAME=plane.internal.example
WEB_URL=http://plane.internal.example
TRUSTED_PROXIES=127.0.0.1/32 172.16.0.0/12
# plus every SERVICE_* secret docker-compose-orca.yml declares required
```

`TRUSTED_PROXIES` is space-separated; Caddy rejects a comma-separated list at
startup. Keeping the copy verbatim is what makes it cheap to refresh
from a new commit:

```bash
git show <commit>:docker-compose-orca.yml > /opt/stacks/<name>/compose.yaml
```

Anything you would have been tempted to edit in that copy belongs in the
override instead — otherwise the next refresh is a merge instead of a diff.

## Updating

```bash
sudo /opt/stacks/<name>/update.sh --dir /opt/stacks/<name> sha-<commit>
```

The tag has to be `sha-<commit>`. `:latest` and `:stage` move, so a host that
deploys them cannot answer "which commit is running" a week later, and two
hosts that pulled on different days run different code while reporting the same
tag.

The script refuses a mutable tag, waits for `api` to become healthy, and then
prints `orca_build_info` for the three containers that share the api image plus
the digest every container actually resolved to. Those three have to agree: a
worker left behind on an older image keeps applying last week's rules while the
API serves this week's.

One nuance the runbook now records: for a commit that was only re-tagged — the
promotion path copies a digest rather than rebuilding — `orca_build_info`
reports the commit that *built* the image, which is not the commit in the tag.
The equality that means something is between the three services and between the
digests, not against the tag string.
