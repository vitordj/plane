## 🚀 Release Candidate Promotion

This pull request promotes tested code changes from the **`stage`** branch to the **`prod`** (Production) branch.

### 📝 Release Info

- **Source:** `stage` ➡️ **Destination:** `prod`
- **Release Version:** _Automated after merge via `release-please`_

---

### 🚀 QA & Production Readiness Checklist

- [ ] **Staging Verified**: Staging environment build and deployment have been fully verified and tested.
- [ ] **Database Migrations**: Any database migrations (Django) have been reviewed, are safe to apply, and have been prepared.
- [ ] **Coolify Environment variables**: New environment variables (if any) are configured in the Coolify production application.
- [ ] **Commit Hygiene**: Checked that all custom commits use correct Conventional Commit format with `orca` scoping (e.g., `feat(orca):`, `fix(orca):`, etc.) so the changelog generates correctly.

---

> [!IMPORTANT]
> **Merging this PR does not deploy anything.** The release happens in two
> steps, and this is the first one. See [docs/release-runbook.md](../../docs/release-runbook.md).
>
> 1. Merging this PR into `prod` runs `release-please`, which opens a **second**
>    PR against `prod` with the version bump and the changelog.
> 2. **Squash-merging that release PR** is what deploys: its title
>    (`chore(prod): release <version>`) becomes the commit message, and
>    `prod.yml` promotes only on that message. An ordinary merge commit says
>    "Merge pull request #N …" and nothing is promoted.
> 3. Promotion resolves each image by the digest built from the `stage` commit
>    this release contains, and refuses — before retagging anything — if
>    `:stage` has drifted from it.
> 4. Coolify then redeploys production, and `prod` is merged back into `stage`.
