# Cloud Agent base image for the Plane (Orca) monorepo.
#
# Installs the stable system toolchains and backing services the local dev stack
# needs so that repository bootstrap (see install.sh) and per-boot service
# startup (see start.sh) have everything available:
#   - PostgreSQL 16          (primary database)
#   - Redis                  (cache / channels / celery result backend)
#   - RabbitMQ               (celery broker)
#   - MinIO + mc             (S3-compatible object storage for uploads/cover images)
#   - Node.js 22 + corepack  (frontend apps and workspace packages; pnpm is pinned via packageManager)
#   - Python 3 + build deps  (native wheels for the Django API: psycopg, lxml, cryptography, xmlsec)
#
# Deliberately does NOT copy the repository in: Cursor checks out the requested
# revision separately, and repo-dependent work belongs in install.sh.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# System services and build dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git sudo bash \
        postgresql postgresql-contrib \
        redis-server \
        rabbitmq-server \
        python3 python3-venv python3-dev \
        build-essential pkg-config \
        libpq-dev libxml2-dev libxslt1-dev libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 (satisfies the repo's engines >=22.18.0) via NodeSource, then enable
# corepack so the pnpm version pinned in package.json's packageManager is used.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable

# MinIO server + client for local S3-compatible object storage.
RUN curl -fsSL https://dl.min.io/server/minio/release/linux-amd64/minio -o /usr/local/bin/minio \
    && curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc \
    && chmod +x /usr/local/bin/minio /usr/local/bin/mc

# The Cloud Agent runs as the `ubuntu` user (uid 1000, present in the base image);
# grant passwordless sudo so start.sh can manage the system services above.
RUN usermod -aG sudo ubuntu \
    && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-ubuntu \
    && chmod 0440 /etc/sudoers.d/90-ubuntu

USER ubuntu
