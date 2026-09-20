#!/usr/bin/env bash
# Update a plain-Compose deployment of docker-compose-orca.yml to an immutable
# image tag: pull, migrate, up, and then verify that what is running is the
# commit you asked for.
#
# This is section 4 of docs/release-runbook.md made executable. The runbook
# says the deploy job going green only means the platform accepted the
# request; this script is the part that checks.
#
#   ./update.sh --dir /opt/stacks/plane                  # redeploy the TAG already in .env
#   ./update.sh --dir /opt/stacks/plane sha-69f6f597b    # write the new TAG, then update
#
# The stack directory holds the deployment, not this repository: a `compose.yaml`
# (a copy of docker-compose-orca.yml), an optional `compose.override.yaml` for
# whatever is specific to the host, and a `.env` with the secrets. `docker
# compose` picks up both files by name, which is why this script never passes
# `-f`: the override stays the one place host-specific settings live.
#
# The tag must be `sha-<commit>`. `:latest` and `:stage` move, so a host that
# deploys them cannot answer "which commit is in production" later, and two
# hosts that pulled on different days silently run different code.
set -euo pipefail

STACK_DIR="${PLANE_STACK_DIR:-}"
TAG_ARG=""
API_SERVICES="api worker beat-worker"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) STACK_DIR="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    sha-*) TAG_ARG="$1"; shift ;;
    *) echo "unexpected argument: $1" >&2; usage 2 ;;
  esac
done

[ -n "$STACK_DIR" ] || { echo "no stack directory: pass --dir or set PLANE_STACK_DIR" >&2; exit 2; }
cd "$STACK_DIR"
[ -f .env ] || { echo "$STACK_DIR/.env not found" >&2; exit 1; }

# Docker needs root on most hosts. Checking here beats failing halfway, with
# the migrator already started and the rest refused.
if ! docker info >/dev/null 2>&1; then
  echo "cannot talk to the Docker daemon (try sudo)" >&2
  exit 1
fi

if [ -n "$TAG_ARG" ]; then
  grep -qE '^TAG=' .env || { echo "no TAG= line in $STACK_DIR/.env to rewrite" >&2; exit 1; }
  sed -i "s|^TAG=.*|TAG=$TAG_ARG|" .env
fi

TAG=$(grep -E '^TAG=' .env | cut -d= -f2-)
case "$TAG" in
  sha-*) ;;
  *) echo "TAG=$TAG is mutable; production is promoted from a commit, not from a moving tag" >&2; exit 1 ;;
esac
echo "==> TAG=$TAG  ($(date -Is))"

echo "==> pull"
docker compose pull --quiet

# `migrator` is `restart: no` and runs once on `up`; api, worker and beat block
# on `wait_for_migrations` until it is done, so there is nothing to sequence
# here by hand.
echo "==> up"
docker compose up -d --remove-orphans

echo "==> waiting for api to become healthy (up to ${HEALTH_TIMEOUT}s)"
api_cid=$(docker compose ps -q api)
[ -n "$api_cid" ] || { echo "no api container" >&2; exit 1; }
st=starting
for _ in $(seq 1 $((HEALTH_TIMEOUT / 5))); do
  st=$(docker inspect -f '{{.State.Health.Status}}' "$api_cid" 2>/dev/null || echo starting)
  [ "$st" = healthy ] && break
  sleep 5
done
echo "api: $st"

docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'

# Runbook section 4: the three containers sharing the api image have to report
# the same build. A worker left behind on an older image keeps applying last
# week rules while the API serves this week.
#
# `git_sha` here is the commit that BUILT the image, which is not always the
# commit in TAG: promotion re-tags a digest, so a commit that only moved a tag
# reports the commit that produced the bits. The equality that means something
# is between the three services, and against the digest -- printed below -- not
# against the tag string.
echo "==> orca_build_info (all three must agree)"
for s in $API_SERVICES; do
  printf '%-12s ' "$s"
  docker compose exec -T "$s" python manage.py orca_build_info 2>/dev/null | tr '\n' ' ' || printf '(no answer)'
  echo
done

# `RepoDigests` is a field of the IMAGE, not of the container: asking
# `docker inspect <container>` for it fails with "map has no entry for key
# RepoDigests" and, under `set -e`, takes the whole script with it. The
# container knows its image id; the image knows its digest.
echo "==> running digests"
docker compose ps -q | while read -r cid; do
  name=$(docker inspect --format '{{.Name}}' "$cid")
  image_id=$(docker inspect --format '{{.Image}}' "$cid")
  digest=$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}(no digest: built locally){{end}}' "$image_id")
  printf '%-16s %s\n' "${name#/}" "$digest"
done

[ "$st" = healthy ] || { echo "FAILED: api never became healthy" >&2; exit 1; }
echo "==> ok"
