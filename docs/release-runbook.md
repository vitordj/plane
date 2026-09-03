# Release runbook

How a change reaches production, what to check while it does, and how to get
back if it should not have. Written to be followed by someone who has never
done a release here: every step is a command or a button, never "deploy it".

Companion documents: [FORK.md](../FORK.md) for the branch model,
`.github/workflows/stage.yml` and `.github/workflows/prod.yml` for what the
automation actually runs.

---

## 0. Prerequisites (once per repository)

- **Settings → Actions → General → Workflow permissions**: "Allow GitHub
  Actions to create and approve pull requests" must be **on**. Without it the
  `Ensure Release Candidate PR` job fails with a clear error — since P0.4 it
  no longer passes silently while creating nothing.
- **Secrets**: `RELEASE_PLEASE_TOKEN` (a PAT that can open PRs on `prod`),
  `COOLIFY_DEPLOY_URL` and `COOLIFY_API_TOKEN` in both the `stage` and `prod`
  GitHub environments.
- **Variables**: `COOLIFY_DEPLOY_ENABLED = true` to enable deploys,
  `RELEASE_ASSIGNEE` for who gets assigned the release-candidate PR.

## 1. Versioning: who decides the number

The version is `<fork>-plane.<upstream>`, e.g. `1.4.0-plane.1.4.1`.

- The **fork part** is Release Please's. It reads the `feat(orca):` /
  `fix(orca):` commits since the last release and bumps accordingly. Do not
  edit it by hand — `1.3.0-plane.1.4.1 → 1.4.0-plane.1.4.1` was computed, and
  a manual edit only confuses the next computation.
- The **upstream part** is yours, and changes only when the fork syncs with a
  new Plane CE release. Update it in the same PR as the sync, in **both**
  `package.json` and `.github/release-please-manifest.json`, and nowhere else.

## 2. Cut a release candidate

You usually do nothing: every push to `stage` runs the `Ensure Release
Candidate PR` job, which opens (or reuses) a PR from `stage` to `prod`, titles
it `release: Promote Release Candidate from v<version>`, applies the
`release_candidate.md` checklist and assigns it.

If `stage` is level with `prod` the job says so and exits — that is success,
not a failure.

Then, on that PR:

1. Work the checklist. The database migration line is the one that repays the
   attention: read the migrations in the diff and decide whether they are safe
   to apply to a live database, not just whether they exist.
2. Note any new environment variable. It has to exist in Coolify **before** the
   deploy, not after.

## 3. Merge the release candidate

Merging `stage` into `prod` does **not** deploy anything. It runs Release
Please, which opens a second PR against `prod` — the release PR — carrying the
version bump and the changelog.

## 4. Merge the release PR — this is the deploy

> **Squash-merge it.** `prod.yml` promotes only when the pushed commit message
> contains `chore(prod): release`, which is the release PR's title. A squash
> merge uses that title as the commit message; an ordinary merge commit says
> "Merge pull request #N …" instead, and nothing promotes. If that happens,
> nothing is broken — re-run the promotion by pushing an empty commit titled
> `chore(prod): release <version>`, or re-run the workflow from the Actions
> tab against the right commit.

Merging it starts, in order:

1. **Resolve Images To Promote** — finds the newest `stage` commit this release
   contains, and for each of the six services the `:sha-<commit>` image built
   from it, then checks that `:stage` still points at that same digest. If any
   service is missing an image, or `:stage` has drifted, the job fails **before
   any retag**. See §6.
2. **Promote Docker Images** — pulls each image by digest and pushes it as
   `:latest`, `:<version>` and `:v<version>`.
3. **Record Promoted Digests In Release Notes** — writes the digest table into
   the GitHub Release. This is what §7 rolls back to.
4. **Deploy to Production (Coolify)** — triggers the deploy and polls it to
   completion.
5. **Sync Prod to Stage** — merges `prod` back into `stage`.

## 5. Verify the deploy

In this order, because each answers a different question:

1. The workflow summary: the promoted digest table, and the Coolify deployment
   status.
2. `docker manifest inspect ghcr.io/<owner>/<repo>/api:v<version>` — the digest
   matches the table.
3. The application: sign in, open a project, create a work item. A green
   pipeline with a broken sign-in is a green pipeline.
4. API logs for the first few minutes: a migration that failed to apply shows
   up here first.

## 6. When promotion refuses

The failure message names the case:

| Message | What happened | What to do |
| --- | --- | --- |
| `<service> has no :sha-<commit> image in the promoted history` | That service never built successfully for any commit in this release | Look at the `stage` run for that commit; rebuild by pushing to `stage`, or run the workflow manually |
| `<service>:stage is not the image built for <sha>` | `:stage` moved after the release candidate — something was pushed to `stage` mid-release | Re-cut the release candidate so the release contains that commit too |
| `<service>:stage does not exist in the registry` | The image was never pushed, or was deleted from GHCR | Rebuild it from `stage` |
| `No stage commit is an ancestor of this release commit` | `prod` carries commits that never went through `stage` | Do not force it. Find how they got there first |

Nothing is retagged in any of these cases, so production keeps running what it
was running.

## 7. Rollback

Rollback means putting the **previous release's digests** back behind
`:latest`. Take them from that release's GitHub Release notes (the digest
table §4.3 wrote), then, logged in to GHCR:

```bash
OWNER_REPO=<owner>/<repo>          # lowercase
REGISTRY=ghcr.io

# One line per service, digests from the previous release's notes:
declare -A DIGESTS=(
  [web]=sha256:...
  [admin]=sha256:...
  [api]=sha256:...
  [space]=sha256:...
  [live]=sha256:...
  [proxy]=sha256:...
)

for service in "${!DIGESTS[@]}"; do
  image="${REGISTRY}/${OWNER_REPO}/${service}"
  docker pull "${image}@${DIGESTS[$service]}"
  docker tag "${image}@${DIGESTS[$service]}" "${image}:latest"
  docker push "${image}:latest"
done
```

Then redeploy in Coolify (or re-run the deploy job), and verify as in §5.

**Database migrations do not roll back with the images.** If the release
applied a migration that the previous image cannot work against, rolling back
the images is not enough — that is why §2 asks you to read the migrations
before merging, and why a destructive migration should ship in its own release
after the code that stopped depending on the column.

## 8. New environment variables

Every release that introduces one is a release that can fail on a machine that
does not have it. Before merging the release PR, check the diff for changes to
`.env.example`, `apps/api/.env.example` and `docker-compose-orca.yml`, and add
anything new to the Coolify application first. `TRUSTED_PROXIES` is required
since P0.7: the proxy will not start without it.

## 9. Rehearsal log

A runbook nobody has followed is a guess. Record each rehearsal here.

| Date | Who | What was exercised | Duration | What went wrong |
| --- | --- | --- | --- | --- |
| _(pending)_ | | RC → promotion by digest → staging deploy → rollback | | |
