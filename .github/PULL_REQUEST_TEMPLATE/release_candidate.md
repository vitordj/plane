## 🚀 Release Candidate Promotion

This pull request promotes tested code changes from the **`stage`** branch to the **`prod`** (Production) branch.

Full procedure, including verification and rollback: [`docs/release-runbook.md`](../../docs/release-runbook.md).

### 📝 Release Info

- **Source:** `stage` ➡️ **Destination:** `prod`
- **Release Version:** _Proposed by `release-please` in a second PR after this one merges_

---

### 🚀 QA & Production Readiness Checklist

- [ ] **Staging Verified**: Staging environment build and deployment have been fully verified and tested — CI green is not QA.
- [ ] **Database Migrations**: Any database migrations (Django) have been reviewed, are safe to apply, and have been prepared. Note that a migration does not roll back with the image (runbook §6).
- [ ] **Environment variables**: New environment variables (if any) are configured in the production environment **before** the deploy job runs (runbook §5).
- [ ] **Commit Hygiene**: Checked that all custom commits use correct Conventional Commit format with `orca` scoping (e.g., `feat(orca):`, `fix(orca):`, etc.) so the changelog generates correctly.

---

> [!IMPORTANT]
> **Merging this PR does not deploy anything.** Promotion happens in two steps,
> and this is the first one:
>
> 1. Merging this PR lands the changes on `prod` and triggers `release-please`,
>    which opens a **second** PR with the version bump and the changelog.
>    Check the version it proposes before merging it — the fork's
>    `-plane.<upstream>` suffix is a semver prerelease tag and is the part most
>    likely to be dropped (runbook §2).
> 2. Merging **that** PR creates the `chore(prod): release X.Y.Z` commit, which
>    is what the `prod.yml` workflow keys on. It promotes the images of the
>    stage commit this release carries — by digest, from the immutable
>    `:sha-<commit>` tags, never from the moving `:stage` tag — then deploys
>    and syncs `prod` back into `stage`.
>
> After the deploy, verify with `GET /api/orca/build-info/` and
> `manage.py orca_build_info` in the api, worker and beat containers: all three
> must report the same commit (runbook §4).
