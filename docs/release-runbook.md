# Release runbook (Orca)

How a commit becomes the production deployment, and how to undo it.

The chain has one rule: **what production runs is the artifact stage CI built
for a specific commit, identified by digest.** Every step below exists to keep
that true, or to prove it after the fact.

> Sections marked *(P0.13)* are not written yet — the release rehearsal, the
> per-release environment-variable checklist and the Release Please decision
> belong to that item on the
> [execution board](./plans/orca-work-management/P0-platform-hardening.md).

## The tags, and which of them may move

| Tag | Written by | Moves? | Means |
| --- | --- | --- | --- |
| `:pr-<number>-<sha>` | a pull request build | no | this PR compiled. Never pushed — the image is built and discarded |
| `:stage` | push to `stage` | **yes** | whatever staging runs right now |
| `:sha-<commit>` | push to `stage` | no | the artifact built for exactly this commit |
| `:latest`, `:<version>`, `:v<version>` | the promotion job | **yes** | what production runs right now |

Only `:sha-<commit>` and the digest itself are safe to reason about after the
fact. The promotion reads `:sha-<commit>` and never `:stage`.

A service whose paths did not change is not rebuilt, so the stage workflow
gives it the commit's `:sha-<commit>` tag pointing at the digest `:stage`
already had. The `image-digests` artifact of each stage run records which of
the six were rebuilt (`"rebuilt": true`) and which were inherited.

## Releasing

1. **Stage is green.** The `Staging CI/CD` run for the commit finished, and its
   `image-digests` artifact lists all six services.
2. **Release candidate PR** (`stage` → `prod`) is open — the `promote-rc` job
   maintains it. Review it as a PR: it is the last human gate.
3. **Merge it with a merge commit, never a squash.** The promotion finds the
   commit under release as the second parent of that merge. A squashed RC has
   no second parent and the job stops with an error telling you to re-run with
   an explicit SHA.
4. **Release Please** opens its release PR; merging it creates the
   `chore(prod): release` commit that triggers `Production CI/CD`. *(P0.13)*
5. **The promotion job** resolves the stage commit, verifies that all six
   `:sha-<commit>` images exist — writing no tag until every one is found —
   and then re-tags each by digest to `:latest`, `:<version>` and
   `:v<version>`. Re-tagging by digest publishes the same bytes; pulling and
   pushing again would not.
6. **Check the run summary.** It shows the stage commit and the six digests
   promoted. The same list lands in the GitHub Release notes and in the
   `promoted-digests` artifact.
7. **Deploy** runs (Coolify), if `COOLIFY_DEPLOY_ENABLED` is `true`.

### After the deploy

Confirm the environment is running what was promoted:

```bash
# On the host, for each service container:
docker inspect --format '{{.Config.Image}} {{index .RepoDigests 0}}' api
```

The digest must be one of the six in the run summary. *(P0.15 will expose the
commit from inside the container, so this check stops depending on the host.)*

### Promoting a specific commit by hand

Rehearsal, or re-promotion after an incident: run `Production CI/CD` from the
Actions tab and give the stage commit in the `stage_sha` input. The job still
refuses any commit that is not an ancestor of `origin/stage`, and still
requires all six images to exist.

## Rolling back

Rollback is a re-promotion of the previous digests — never a rebuild, which
would produce different bytes.

1. Find the previous release's digests: the `promoted-digests` artifact of the
   previous `Production CI/CD` run, or the "Production Container Artifacts"
   table in the previous GitHub Release.
2. Re-point the production tags at them:

   ```bash
   docker login ghcr.io
   IMAGE_BASE=ghcr.io/vitordj/plane
   # one line per service, with that release's digest:
   docker buildx imagetools create --tag $IMAGE_BASE/api:latest $IMAGE_BASE/api@sha256:<digest>
   ```

   Alternatively, and preferably, run `Production CI/CD` with `stage_sha` set
   to the previous release's stage commit: it re-promotes the same six digests
   and updates every version tag consistently.
3. Redeploy in Coolify so the containers pull the tags again.
4. If the rollback crosses a database migration, the migration is *not* undone
   by any of this. Check what the release changed before rolling back.

## Environment variables introduced by a release

*(P0.13)* — the checklist of variables a release requires, and where each one
is set in Coolify.

## Rehearsal log

*(P0.13)* — the end-to-end rehearsal (RC PR, promotion, staging deploy,
rollback) with date, duration and what went wrong. Not run yet.
