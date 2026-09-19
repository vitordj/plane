# Release runbook

How a commit on `stage` becomes the image running in production, what to check
at each step, and how to get back if it goes wrong.

Read this once before your first release. The two things worth internalizing:
**production is promoted from a commit, not from a moving tag** (P0.3), and
**every step has a check that can fail** — a release that skipped its checks is
not faster, it is just unverified.

> **Status:** written alongside the release-chain work (P0.1–P0.3, P0.15,
> P0.16). The end-to-end rehearsal called for by P0.13 has **not** happened
> yet; the rehearsal log at the bottom is empty on purpose, and the timings are
> estimates. §2 no longer carries the version-bump warning it was written with:
> that question has been answered against the tool, and what it actually does
> is recorded there.

---

## The flow, in one picture

```text
push to stage
  └─ Staging CI/CD: lint, tests, ruff, build
       ├─ publishes <img>:stage and <img>:sha-<commit> for all six services
       ├─ artifact image-digests: the digest each service resolved to
       └─ opens/refreshes the Release Candidate PR (stage → prod)

merge the RC PR into prod
  └─ Release Please opens a Release PR on prod (version bump + changelog)

merge the Release PR   →  commit "chore(prod): release X.Y.Z" lands on prod
  └─ Production CI/CD
       ├─ resolves the stage commit this release carries (merge-base)
       ├─ refuses unless all six :sha-<commit> images exist
       ├─ copies those digests to :latest, :X.Y.Z and :vX.Y.Z
       └─ deploys, then syncs prod back into stage
```

Nothing in this chain rebuilds code. What production runs is byte-for-byte
what CI built from the reviewed commit.

---

## 1. Before you start

- [ ] `stage` is green: the Staging workflow succeeded on its current head.
- [ ] Staging has actually been used since the last deploy — CI green is not QA.
- [ ] You know which environment variables this release adds. Grep the diff for
      new `os.environ` / `VITE_` / `${...}` reads and list them; §5 is where
      they get set.
- [ ] Any Django migration in the diff has been reviewed for lock behaviour on
      a table with production-sized data.

## 2. Cut the release candidate

The Staging workflow keeps an RC pull request open from `stage` to `prod`
whenever `stage` is ahead. It is created by the `Ensure Release Candidate PR`
job, which fails loudly if it cannot create one (P0.4) — if there is no RC PR
and the job is green, something is wrong; read the job log rather than opening
the PR by hand.

1. Open the RC PR and walk its checklist.
2. Merge it into `prod`.
3. Wait for **Release Please** to open a Release PR on `prod`.

   **If no Release PR appears and the workflow is green, it ran without a
   token.** `release-please.yml` passes `token:` to the action; an unset
   secret expands to an empty string, which the action accepts as a token it
   was given rather than one it was not, and it then does nothing — no PR, no
   `chore(prod): release` commit, and `Production CI/CD` skipped, because that
   workflow only promotes on that commit message. This is what happened on
   19/09/2026 (run 35455579399): `prod` moved and nothing was promoted. The
   remedy is either a `RELEASE_PLEASE_TOKEN` secret or a
   `|| secrets.GITHUB_TOKEN` fallback in that workflow; with the fallback,
   "Allow GitHub Actions to create and approve pull requests" has to stay on.

4. **Check the version it proposes before merging.** The fork's convention is
   `v<fork version>-plane.<upstream version>` (FORK.md), which semver reads as
   a prerelease suffix, and the configuration sets no prerelease strategy — so
   this runbook used to warn that the first release might **drop** the
   `-plane.<upstream>` suffix.

   **It does not.** Checked against release-please 17.9.0, the version
   `googleapis/release-please-action@v4` bundles, with this repository's
   config, bumping from the current manifest value `1.5.0-plane.1.4.2`:

   | Commits on the release | Proposed version |
   | --- | --- |
   | `fix` only | `1.5.1-plane.1.4.2` |
   | `fix` + `feat` | `1.6.0-plane.1.4.2` |
   | breaking change | `2.0.0-plane.1.4.2` |

   `DefaultVersioningStrategy` carries the prerelease component through the
   bump untouched; it is the numeric core that moves. The resulting tag
   (`include-v-in-tag`) is e.g. `v1.6.0-plane.1.4.2`, which is a valid Docker
   tag, so the `:X.Y.Z` and `:vX.Y.Z` names the promotion job writes are fine.

   Two consequences worth knowing rather than discovering:

   - The suffix only changes when an upstream sync changes it (P0.11 sets it by
     hand in `.github/release-please-manifest.json`). Release Please will never
     advance it on its own, which is correct — it tracks upstream, not this
     fork's release count.
   - Every such version is a semver **prerelease**, so it sorts *before* the
     plain `1.6.0` that will never exist, and GitHub marks the Release as a
     prerelease. That is a property of the convention in FORK.md, not a
     misconfiguration.

   The Release PR is still editable before merge, and it remains the place to
   look. If a future upstream sync makes the proposal wrong, fix it there and
   record what was done in the rehearsal log below.
5. Merge the Release PR. That is the commit — `chore(prod): release X.Y.Z` —
   that both promotion jobs key on.

## 3. Watch the promotion

The `Promote Images (Stage -> Production)` job:

1. resolves the stage commit the release carries (`git merge-base` of `prod`
   and `stage`);
2. resolves the digest of `<img>:sha-<commit>` for all six services **before
   writing any tag**, and fails naming the missing ones — a half-promoted
   release (three services new, three old) is worse than none;
3. copies those digests onto `:latest`, `:X.Y.Z` and `:vX.Y.Z` with
   `docker buildx imagetools create`, which preserves multi-arch manifests;
4. writes the stage commit and every promoted digest into the run summary and
   the GitHub Release body.

**If it fails with "no CI-published image for: …"**, the commit being promoted
was never built by the Staging workflow — most likely it predates the
`:sha-<commit>` tags. Run the Staging workflow on that commit via
`workflow_dispatch`, then re-run this job.

Keep the promoted digest list. §6 is where you need it.

## 4. Verify the deployment

Not "the deploy job went green" — that only means the platform accepted the
request.

On a plain Compose host, `deployments/compose/update.sh` does all of the
below in one pass — see [deployments/compose](../deployments/compose/README.md).
By hand:

```bash
# 1. The container is running the image we think it is.
docker inspect --format '{{.Config.Image}} {{index .RepoDigests 0}}' api

# 2. The image is the commit that was released (P0.15).
#    Through the API, as an instance admin:
curl -s -b "$SESSION_COOKIE" https://<host>/api/orca/build-info/
#    And in the containers with no HTTP surface:
docker compose exec api          python manage.py orca_build_info
docker compose exec bgworker     python manage.py orca_build_info
docker compose exec beatworker   python manage.py orca_build_info
```

All three must report the **same** `git_sha` and the **same**
`orca_org_units_enabled`. A worker left behind on an older image is the failure
mode this check exists for: it keeps writing `ProjectMember` rows with last
week's rules while the API serves this week's.

**`git_sha` is the commit that built the image, not the commit in the tag.**
Promotion (§3) copies a digest onto a new tag; it never rebuilds. So for a
release whose stage commit only changed files no service Dockerfile sees — a
docs-only commit, say — the images were retagged rather than rebuilt, and all
three containers correctly report the *earlier* commit that produced those
bits. That is not drift. The two equalities that mean something are: the three
services agree with each other, and the digest running on the host is the
digest the promotion job wrote. `deployments/compose/update.sh` prints both and
does not fail on a `git_sha` that differs from the tag, deliberately.

```bash
# 3. The organizational kill switch agrees across services (P0.14).
for s in api bgworker beatworker; do docker compose exec "$s" printenv ORCA_ORG_UNITS_ENABLED; done
```

Then click through: sign in, open a project, create a work item. Sign-in is
worth doing deliberately when the release touches authentication — an Entra
misconfiguration surfaces as a redirect loop, not as a failed health check
(`docs/entra-directory-sync.md` §Troubleshooting).

## 5. Environment variables

New variables do not appear by themselves, and a container that boots without
one usually fails later, under load, in a way that does not point at the cause.

- [ ] Every variable added by this release is set in the production
      environment **before** the deploy job runs.
- [ ] `TRUSTED_PROXIES` is set to the real CIDR of the ingress in front of
      Caddy. The stack refuses to start without it, by design (P0.7).
- [ ] `ORCA_ORG_UNITS_ENABLED` has the same value everywhere (see §4).
- [ ] Anything secret was set through the platform's secret store, not through
      a committed file.

## 6. Rollback

Rolling back is re-promoting the previous digests. It does not rebuild
anything and does not need a revert commit.

```bash
IMAGE_BASE=ghcr.io/vitordj/plane
PREVIOUS_SHA=<the stage commit of the release you are going back to>

for app in web admin api space live proxy; do
  docker buildx imagetools create \
    --tag "${IMAGE_BASE}/${app}:latest" \
    "${IMAGE_BASE}/${app}:sha-${PREVIOUS_SHA}"
done
```

Then redeploy so the platform pulls `:latest` again.

Where to find `PREVIOUS_SHA`, in order of convenience:

1. the previous GitHub Release body — the promotion job writes the stage commit
   and the digests into it;
2. the `image-digests` artifact of the Staging run for that commit (90-day
   retention);
3. `git log prod` — the release commits are on that branch.

**A database migration does not roll back with the image.** If the release
contained one, check whether the previous image can run against the migrated
schema before rolling back. Additive migrations (new nullable column, new
table) are usually safe to leave in place; a migration that dropped or renamed
something is not, and the rollback becomes a restore-from-backup decision
rather than a retag.

## 7. Maintenance the release depends on

- **Pinned base images.** `docker-compose-orca.yml` pins PostgreSQL, Valkey,
  RabbitMQ and MinIO to immutable tags (P0.16). Bump one deliberately, in its
  own commit, never as a side effect of a release. For MinIO, pick a
  `RELEASE.<timestamp>Z` tag from the registry rather than tracking `latest`.
- **CI and deployment run the same PostgreSQL major** (15.7). If that ever
  changes, all four places change together — see
  `apps/api/tests/RUNNING_TESTS.md`.
- **`:stage` is a convenience pointer** for the staging environment only.
  Nothing in the production path reads it.

---

## Rehearsal log

Fill one row per full rehearsal or real release. The first row closes the
P0.13 acceptance criterion.

| Date | Version | Stage commit | Duration (RC → verified) | What went wrong | Runbook change |
| --- | --- | --- | --- | --- | --- |
| — | — | — | — | — | — |
