# Agent Development Guide

## Commands

- `pnpm dev` - Start all dev servers (web:3000, admin:3001)
- `pnpm build` - Build all packages and apps
- `pnpm check` - Run all checks (format, lint, types)
- `pnpm check:lint` - OxLint across all packages
- `pnpm check:types` - TypeScript type checking
- `pnpm fix` - Auto-fix format and lint issues
- `pnpm turbo run <command> --filter=<package>` - Target specific package/app
- `pnpm --filter=@plane/ui storybook` - Start Storybook on port 6006

## Code Style

- **Imports**: Use `workspace:*` for internal packages, `catalog:` for external deps
- **TypeScript**: Strict mode enabled, all files must be typed
- **Formatting**: oxfmt, run `pnpm fix:format` (Frontend); Ruff is configured for Python formatting (`line-length = 120`, double quotes).
- **Linting**: OxLint with shared `.oxlintrc.json` config (Frontend); Ruff is used for Python linting (`E`, `F` rules) under `apps/api/`.
  - _Weighing Suppressions vs. Fixes_: Always prioritize fixing the root cause of lint warnings/errors (e.g. converting `div role="button"` to semantic `<button>` elements, or writing proper typescript types).
  - _Suppression Escape Hatch_: Use `eslint-disable` (specifically inline `eslint-disable-next-line` along with standard accessibility helpers) only as a last resort if resolving the root issue introduces high regression risks (such as breaking CSS layouts) or if changing HTML tag structures increases the surface area for merge conflicts when syncing with the upstream repository. Never use file-wide disables when a line-specific override suffices.
- **Naming**: camelCase for variables/functions, PascalCase for components/types
- **Error Handling**: Use try-catch with proper error types, log errors appropriately
- **State Management**: MobX stores in `packages/shared-state`, reactive patterns
- **Testing**: All features require unit tests, use existing test framework per package
- **Components**: Build in `@plane/ui` with Storybook for isolated development
- **Copyright Headers**: All new Python/TS/TSX files must include the standard copyright header via `addlicense` (see [COPYRIGHT_CHECK.md](./COPYRIGHT_CHECK.md)).
- **Documentation & Comments**: Write clear, easy-to-understand JSDoc comments or docstrings for all modifications. Ensure they match existing codebase formats (e.g. using `@description`, `@param`, `@returns` structures). Never delete or omit existing JSDoc comments/docstrings when refactoring or modifying code. Always comment custom overrides, sidecar functions, and parallel cycle changes clearly to guide subsequent developers and AI agents.

## Fork & Customization Strategy

All changes must follow the upstream compatibility model detailed in [FORK.md](./FORK.md):

- **Upstream Syncing**: Never commit custom logic or branding changes directly to the `upstream` branch. As an agent, always read and explicitly verify [FORK.md](./FORK.md) before implementing changes.

- **Non-Destructive Branding**: Do not rewrite React component imports for logos/assets. Instead, use asset overrides or inject custom CSS.
  - Logo SVG react components are located in [packages/propel/src/icons/brand](./packages/propel/src/icons/brand) (e.g. `plane-logo.tsx`, `plane-lockup.tsx`, `plane-wordmark.tsx`).
  - Public branding assets are located in [apps/web/public/plane-logos](./apps/web/public/plane-logos).
  - _Design Cohesion_: As much as possible, reuse existing components, styles, and themes (from `@plane/ui` and `@plane/propel`) to align with the repository's current structure. Avoid creating separate or ad-hoc custom designs that disrupt theme consistency.

- **Wrapper Architecture**: Implement large/complex custom features as external sidecars/services, communicating with Plane via REST APIs and Webhooks.
- **Feature Toggles**: Disable unwanted core features using config or `.env` flags instead of deleting code blocks. Note that the frontend is a Vite-based react app, and env variables must be prefixed with `VITE_`.
  - _UI_: Hide unwanted elements using CSS or React display conditions.
  - _Backend_: NEVER delete database migration files or drop core tables directly to disable a feature.
- **Database Schema**: Do not modify core database tables. Prefer storing metadata in JSON fields or creating a separate relational sidecar model (e.g. `CustomIssueProperties`) to avoid migration conflicts.
- **Custom API Routes**: Register custom endpoints under a unique routing prefix (e.g., `/api/v1/custom/`) rather than modifying standard Plane route registries directly.
- **Versioning**: Tag custom releases using the format `v[ForkVersion]-plane.[UpstreamVersion]` (e.g. `v1.0.0-plane.1.3.1`) to track both upstream and custom releases.
- **Commit Prefixes**: Use standard Conventional Commit prefixes with `orca` scoping to enable accurate release-please version bumping and changelog grouping:
  - `feat(orca): [short description]` — Custom features, integrations, or sidecars. (Minor bump)
  - `fix(orca): [short description]` — Bug fixes for custom code. (Patch bump)
  - `style(orca-ui): [short description]` — Branding, logo, or color changes.
  - `style(orca): [short description]` — Custom UI spacing or style improvements.
  - `docs(orca): [short description]` — Documentation updates.
  - `chore(orca): [short description]` — Development setup and dependency management.
  - `refactor(orca): [short description]` — Code refactoring or cleanup.
- **Atomic Commits**: Keep edits small and write semantic, isolated commits to make merging upstream updates easier.

## Active plans

- **Gestão de trabalho por área (Orca):** specification in
  [docs/orca-work-management-rfc.md](./docs/orca-work-management-rfc.md);
  execution board, per-phase work items and the session handoff prompt in
  [docs/plans/orca-work-management/](./docs/plans/orca-work-management/README.md).
  Pick the next `[ ]` item from the board; one item, one PR against `stage`.

## Token Efficiency & Command Guidelines

- **Never Let Output Into The Context**: What costs tokens is the output, not the run. Commands that print thousands of lines (`pnpm check`, `pnpm check:types`, `pnpm build`, full test suites, Django migrations) must **never** be run with their output going to the agent context. Redirect to a file and read only the summary: `cmd > /tmp/out.log 2>&1; tail -20 /tmp/out.log`.
- **Running Them Is Allowed, And Often Required**: Earlier revisions of this guide said the session could not run these at all. That was a premise, not a fact. A session container without a Docker daemon still has the PostgreSQL and Redis binaries and a warm pnpm store, so `pnpm install --frozen-lockfile`, `pnpm check:types --filter=web` (via turbo — see below), `check:lint`, `check:format`, `check:sync`, `pytest` and `makemigrations --check` all run in-session, in seconds to a couple of minutes. The recipe and the measured timings live in [docs/plans/orca-work-management/HANDOFF-PROMPT.md](./docs/plans/orca-work-management/HANDOFF-PROMPT.md) under "Ambiente local". Prefer running a check over asking the developer to run it: an unverified claim in a report is worth less than a `tail -20`.
- **`check:types` Goes Through Turbo**: `pnpm check:types --filter=web` passes; `pnpm --filter web check:types` on its own fails with thousands of `Cannot find module '@plane/…'`, because the task declares `dependsOn: ["^build"]` in `turbo.json` and the bare `--filter` form skips building the workspace packages it imports. The failure is the missing build, not the code.
- **Provide Actionable Commands**: For what genuinely stays out of reach — Docker, deploy, `git push --delete`, anything needing a database *with data* — list the exact commands for the developer to run locally.
- **Database Migrations**: When database schema updates are made, always remind the developer of the exact commands needed to generate and apply them. They must run `makemigrations` first to build the files, then `migrate` to apply them (e.g., `docker compose exec api python manage.py makemigrations && docker compose exec api python manage.py migrate` or `python3 apps/api/manage.py makemigrations && python3 apps/api/manage.py migrate`).

## Backend tests (Docker)

The Django/pytest suite for `apps/api` runs in an isolated stack defined by `docker-compose-test.yml` at the repo root.

Prereq (once): `./setup.sh` — generates `apps/api/.env` from `.env.example`.

- Full suite: `docker compose -f docker-compose-test.yml up --build --abort-on-container-exit --exit-code-from api-tests`
- Subset: `docker compose -f docker-compose-test.yml run --rm api-tests pytest -m unit`
- Teardown: `docker compose -f docker-compose-test.yml down -v`

See `apps/api/tests/RUNNING_TESTS.md` for the full walkthrough and troubleshooting; see `apps/api/tests/TESTING_GUIDE.md` for test conventions and fixtures.
