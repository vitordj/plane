---
name: create-pull-request
description: Use when creating a pull request for the current branch — gathers branch context, fills the repo's Basic PR template from the actual diff, and creates the PR against `stage` with a Conventional Commit title.
user_invocable: true
---

# Create PR

Create a pull request against `stage` using the repo's Basic PR template, a Conventional Commit title with the `orca` scope, and a fully filled-out description based on the actual diff.

## Workflow

1. **Determine the base branch**: `stage` for features and fixes. Release Candidate promotions to `prod` are opened by the stage workflow itself, not by hand.

2. **Gather context** (in parallel):
   - `git status -s` — check for uncommitted changes
   - `git diff <base>...HEAD --stat` — files changed
   - `git log <base>...HEAD --oneline` — all commits on the branch
   - `git diff <base>...HEAD --no-color` — full diff for understanding changes (if very large, focus on the most important files first)
   - `git rev-parse --abbrev-ref --symbolic-full-name @{u}` — check if branch tracks a remote
   - Read `.github/PULL_REQUEST_TEMPLATE/basic.md` — this is the template to fill. (`.github/pull_request_template.md` only holds instructions for people creating PRs in the GitHub UI, where a workflow pastes `basic.md` into an empty description.)

3. **Draft the PR** using `basic.md`:

   **Title**: `<type>(orca): <concise summary>` (under 70 chars), using the same type prefixes as the commits — `feat`, `fix`, `style`, `refactor`, `docs`, `chore`, `test`, `ci`, `perf`. The scope is `orca` (or `orca-ui` for branding-only changes), matching the checklist in the template and the release-please changelog sections.

   **Body**: fill in every section of `basic.md` from the actual diff:
   - **Description** — Clear, concise summary of what the PR does and why. Focus on the "what" and "why", not line-by-line changes. Mention important implementation decisions, and any migration the deployer has to run.
   - **Type of Change** — Tick the box(es) that apply; several may.
   - **Screenshots and Media** — Add them for visual changes; write "Not applicable" otherwise.
   - **Verification & Testing** — Concrete checks grounded in the actual changes (tests added and how they were run, manual scenarios such as "Navigate to workspace settings → Areas and verify …"). Leave the two template checkboxes unticked unless you actually did them.
   - **References** — Related docs under `docs/`, linked issues or PRs the user mentions, follow-up PRs.

   Append a Claude Code session line at the bottom of the body.

4. **Push and create** (in parallel where possible):
   - Push branch with `-u` if no upstream is set
   - Create PR via the GitHub tooling available in the session (`gh pr create` with a HEREDOC body, or the GitHub MCP `create_pull_request` tool)

5. **Return the PR URL** to the user.

## Example Title

```
fix(orca): close the directory endpoints under the kill switch
```

## Guidelines

- Keep the description concise but informative
- Use bullet points when listing multiple changes
- Focus on user-facing impact, not implementation details
- Don't fabricate test scenarios that aren't relevant to the actual changes
- Do not leave the body empty expecting the workflow to fill it: the workflow only pastes the blank template, and a PR opened by an agent should arrive already filled in

## Common Mistakes

- Summarizing only the latest commit instead of all commits on the branch
- Forgetting to check for an upstream before pushing
- Using a title prefix that is not one of the Conventional Commit types the template lists
- Targeting `prod` directly — everything lands in `stage` first
- Wrapping the PR body in a code fence when passing it to `gh pr create`
