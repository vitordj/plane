# Running the API Test Suite

This guide covers running the Django/pytest suite for `apps/api` inside Docker via `docker-compose-test.yml` at the repo root. The compose file boots an isolated stack — Postgres, Valkey (Redis), RabbitMQ, MinIO — with tmpfs-backed data dirs, so every run begins from a clean slate and a single teardown command removes everything.

For background on the test layout, markers, and fixtures, see [`TESTING_GUIDE.md`](./TESTING_GUIDE.md) and [`README.md`](./README.md).

## Prerequisites

- Docker and Docker Compose v2 (`docker compose ...`)
- Env files generated via the setup script:

  ```bash
  ./setup.sh
  ```

  This copies `apps/api/.env.example` → `apps/api/.env` (along with the other app env files). The compose file reads `apps/api/.env`, so this step must run **before** the first `docker compose` invocation.

## Running the suite

All commands are run from the repo root.

### Full suite

```bash
docker compose -f docker-compose-test.yml up \
  --build \
  --abort-on-container-exit \
  --exit-code-from api-tests
```

- `--build` rebuilds the `api-tests` image when `Dockerfile.dev` or `requirements/*.txt` change.
- `--abort-on-container-exit` stops the dependency services as soon as `api-tests` exits.
- `--exit-code-from api-tests` propagates pytest's exit code so this works in CI.

### Filtered runs

Use `docker compose run` to override the default `pytest` command. Anything you pass after the service name is forwarded to pytest.

```bash
# Only unit tests (marker defined in pytest.ini)
docker compose -f docker-compose-test.yml run --rm --build api-tests pytest -m unit

# A single directory, filtered by name
docker compose -f docker-compose-test.yml run --rm api-tests \
  pytest plane/tests/unit -k "test_workspace"

# Single file with verbose output
docker compose -f docker-compose-test.yml run --rm api-tests \
  pytest plane/tests/unit/models/test_workspace.py -vv
```

The available markers (`unit`, `contract`, `smoke`, `slow`) are declared in `apps/api/pytest.ini`.

### Teardown

```bash
docker compose -f docker-compose-test.yml down -v
```

`-v` removes the ephemeral volumes and the `test_env` network. Because the data directories are tmpfs, no host state survives a teardown — every run starts clean. Run this between unrelated test sessions to free Docker resources.

## How it works

| Service      | Image                                | Purpose                                       |
| ------------ | ------------------------------------ | --------------------------------------------- |
| `test-db`    | `postgres:15.7-alpine`               | Application database                          |
| `test-redis` | `valkey/valkey:7.2.11-alpine`        | Cache / Celery broker                         |
| `test-mq`    | `rabbitmq:3.13.6-management-alpine`  | Task queue                                    |
| `test-minio` | `minio/minio`                        | S3-compatible object storage                  |
| `api-tests`  | built from `apps/api/Dockerfile.dev` | Installs `requirements/test.txt`, runs pytest |

All four dependencies expose health checks; `api-tests` waits for `service_healthy` on each via `depends_on`, so pytest only starts once the stack is ready.

Test-time env overrides live in the compose file itself (`POSTGRES_HOST=test-db`, `REDIS_URL=redis://test-redis:6379/`, `AWS_S3_ENDPOINT_URL=http://test-minio:9000`, `DJANGO_SETTINGS_MODULE=plane.settings.test`). Everything else is inherited from `apps/api/.env`.

## What CI runs

| Where | Command | When |
| --- | --- | --- |
| `stage.yml` → `api_lint` | `ruff check .` and `ruff format --check .` in `apps/api` | Every PR and push that touches `apps/api` |
| `stage.yml` → `api_tests` | `pytest plane/tests/unit -q`, against postgres and valkey service containers | Every PR and push that touches `apps/api` |
| `api-heavy-tests.yml` | `pytest plane/tests/contract plane/tests/smoke -q` inside the full `docker-compose-test.yml` stack | Manually, from the Actions tab |

The pull-request job runs the **whole** unit tree, not only `plane/tests/unit/orca`. This fork edits upstream serializers, views, celery tasks and intake, so a regression it causes in upstream code has to surface on the pull request rather than in production. The unit tests that touch object storage mock `boto3`, so the database and cache containers are enough for them.

The contract and smoke suites are not on that path: they need the message broker and object storage, which means the compose stack and several minutes. Run them by hand before a release candidate, after changing the public API, and after an upstream sync.

### CI exclusions

None. If a directory ever has to be skipped, add the `--ignore` to the `Run API Unit Tests` step in `.github/workflows/stage.yml` **and** list it here with the reason and the condition for removing it:

| Path | Why it is excluded | Removed when |
| --- | --- | --- |
| _(empty)_ | | |

An exclusion nobody can explain is how a suite quietly stops covering.

## Troubleshooting

- **`./apps/api/.env: no such file or directory`** — run `./setup.sh` from the repo root.
- **Port already in use** — none of the test services publish host ports; if you see this it's coming from a different compose stack. Stop the local stack (`docker compose -f docker-compose-local.yml down`).
- **Stale image after dependency changes** — rebuild explicitly: `docker compose -f docker-compose-test.yml build --no-cache api-tests`.
- **MinIO bucket missing** — the `test-minio` entrypoint creates the bucket named by `AWS_S3_BUCKET_NAME` (default `uploads`). Change the value in `apps/api/.env` and re-run.
- **Database state leaking between runs** — confirm you ran `down -v` (not just `down`). The tmpfs mounts are torn down with the container, but the network and any externally created volumes need `-v` to clear.
