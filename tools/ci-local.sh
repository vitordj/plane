#!/usr/bin/env bash
# Run what .github/workflows/stage.yml runs, on a machine you own: the API test
# suite against a real PostgreSQL, then a build of all six service images with
# the same Dockerfiles, contexts and build-args CI uses.
#
# AGENTS.md says Docker is out of reach of an agent session, so nothing in this
# repository could be tried against a running stack before it was pushed. On a
# host that does have Docker, this script is that missing step -- and it is
# also the cheap way to find out that an image does not build, without spending
# a CI run and a push to stage to learn it.
#
#   ./tools/ci-local.sh                      # tests, then build, from the current tree
#   ./tools/ci-local.sh --ref origin/stage   # fetch and detach onto a ref first
#   ./tools/ci-local.sh --tests-only         # suite only
#   ./tools/ci-local.sh --skip-tests         # build only
#   ./tools/ci-local.sh --up /opt/stacks/x   # also `up` a build-based stack in that directory
#
# Full output goes to a log file; the terminal gets the summary. Builds are
# serialised two at a time because four concurrent Node builds exhaust a 16 GB
# host -- raise BUILD_PARALLEL if yours is bigger.
set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
REPO_OWNER="$(stat -c '%U' "$REPO")"
LOGDIR="${LOGDIR:-${TMPDIR:-/tmp}/plane-ci}"
IMAGE_PREFIX="${IMAGE_PREFIX:-plane-local}"
PYTEST_ARGS="${PYTEST_ARGS:-plane/tests/unit -q -m unit -p no:cacheprovider}"
BUILD_PARALLEL="${BUILD_PARALLEL:-2}"

REF=""; RUN_TESTS=1; RUN_BUILD=1; UP_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --skip-tests) RUN_TESTS=0; shift ;;
    --tests-only) RUN_BUILD=0; shift ;;
    --up) UP_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unexpected argument: $1" >&2; exit 2 ;;
  esac
done

docker info >/dev/null 2>&1 || { echo "cannot talk to the Docker daemon (try sudo)" >&2; exit 1; }
mkdir -p "$LOGDIR"

# One run at a time. Two builds of the same image tags racing each other
# produce an image nobody can attribute to a commit.
exec 9>"$LOGDIR/lock"
flock -n 9 || { echo "another ci-local.sh is running (lock: $LOGDIR/lock)" >&2; exit 1; }

LOG="$LOGDIR/$(date +%Y%m%d-%H%M%S).log"
echo "full log: $LOG"
exec > >(tee -a "$LOG") 2>&1

t0=$(date +%s)
step() { printf '\n==> [%5ds] %s\n' "$(( $(date +%s) - t0 ))" "$*"; }
# Never run git as root against someone else checkout: it leaves root-owned
# objects behind and the next ordinary `git` call fails on them.
as_owner() { if [ "$(id -un)" = "$REPO_OWNER" ]; then "$@"; else sudo -u "$REPO_OWNER" "$@"; fi; }
git_repo() { as_owner git -C "$REPO" "$@"; }

if [ -n "$REF" ]; then
  step "git fetch + checkout --detach $REF"
  git_repo fetch --quiet origin
  git_repo checkout --quiet --detach "$REF"
fi
SHA=$(git_repo rev-parse HEAD)
SHORT="${SHA:0:9}"
step "commit $SHA"
git_repo --no-pager log -1 --format='%cs %s'
if [ -n "$(git_repo status --porcelain --untracked-files=no)" ]; then
  echo "WARNING: the tree has local modifications -- the images will not match commit $SHORT"
fi

# What setup.sh copies, minus the pnpm install, which the container builds do
# themselves. The API suite reads apps/api/.env for SECRET_KEY.
step "env files"
for pfx in "" apps/web/ apps/api/ apps/space/ apps/admin/ apps/live/; do
  if [ ! -f "$REPO/${pfx}.env" ] && [ -f "$REPO/${pfx}.env.example" ]; then
    as_owner cp "$REPO/${pfx}.env.example" "$REPO/${pfx}.env"
    echo "created ${pfx}.env"
  fi
done
if [ -f "$REPO/apps/api/.env" ] && ! grep -q '^SECRET_KEY=' "$REPO/apps/api/.env"; then
  as_owner sh -c "printf 'SECRET_KEY=\"%s\"\n' \"\$(tr -dc 'a-z0-9' </dev/urandom | head -c50)\" >> '$REPO/apps/api/.env'"
  echo "appended SECRET_KEY to apps/api/.env"
fi

if [ "$RUN_TESTS" -eq 1 ]; then
  step "API suite via docker-compose-test.yml: pytest $PYTEST_ARGS"
  TEST_LOG="$LOGDIR/pytest-$SHORT-$(date +%H%M%S).log"
  TC=(docker compose -p plane-test -f "$REPO/docker-compose-test.yml")
  "${TC[@]}" build --quiet api-tests
  rc=0
  # shellcheck disable=SC2086
  "${TC[@]}" run --rm api-tests pytest $PYTEST_ARGS > "$TEST_LOG" 2>&1 || rc=$?
  "${TC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  echo "--- tail of $TEST_LOG:"
  tail -n 15 "$TEST_LOG"
  if [ "$rc" -ne 0 ]; then
    echo "FAILED: pytest exited $rc -- nothing was built."
    exit "$rc"
  fi
fi

if [ "$RUN_BUILD" -eq 1 ]; then
  # Same six services, contexts and Dockerfiles as the build-push matrix in
  # stage.yml, and the same two build-args: GIT_SHA and IMAGE_TAG are baked in
  # so the running container can answer which commit it came from (P0.15).
  step "building six images as $IMAGE_PREFIX/<service>:sha-$SHA"
  build_one() {
    local service="$1" context="$2" file="$3"
    docker build \
      --build-arg "GIT_SHA=$SHA" \
      --build-arg "IMAGE_TAG=sha-$SHA" \
      -t "$IMAGE_PREFIX/$service:sha-$SHA" \
      -f "$REPO/$file" "$REPO/$context"
  }
  set -- \
    "web:.:apps/web/Dockerfile.web" \
    "admin:.:apps/admin/Dockerfile.admin" \
    "space:.:apps/space/Dockerfile.space" \
    "live:.:apps/live/Dockerfile.live" \
    "api:apps/api:apps/api/Dockerfile.api" \
    "proxy:apps/proxy:apps/proxy/Dockerfile.ce"
  running=0
  for spec in "$@"; do
    IFS=: read -r service context file <<<"$spec"
    build_one "$service" "$context" "$file" &
    running=$((running + 1))
    if [ "$running" -ge "$BUILD_PARALLEL" ]; then wait -n; running=$((running - 1)); fi
  done
  wait
  docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' | grep -E "^$IMAGE_PREFIX/" | grep -F "sha-$SHORT" \
    || { echo "FAILED: no $IMAGE_PREFIX/* image for sha-$SHORT"; exit 1; }
fi

if [ -n "$UP_DIR" ]; then
  # The stack directory is the host deployment, not this repository: its
  # compose file builds from ${PLANE_SRC} and reads TAG/GIT_SHA from its .env.
  step "up in $UP_DIR"
  cd "$UP_DIR"
  [ -f .env ] || { echo "$UP_DIR/.env not found" >&2; exit 1; }
  sed -i "s|^TAG=.*|TAG=sha-$SHA|; s|^GIT_SHA=.*|GIT_SHA=$SHA|" .env
  docker compose up -d --remove-orphans
  docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
  step "orca_build_info (all three must report $SHORT)"
  for s in api worker beat-worker; do
    printf '%-12s ' "$s"
    docker compose exec -T "$s" python manage.py orca_build_info 2>/dev/null | tr '\n' ' ' || printf '(no answer)'
    echo
  done
fi

step "OK -- commit $SHORT. Log: $LOG"
