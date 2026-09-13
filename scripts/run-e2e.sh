#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=${IMAGE:-localhost/odoo-runner:17.0}
SOCKET=${DOCKER_HOST:-"unix://$(podman info --format '{{.Host.RemoteSocket.Path}}')"}

command -v act >/dev/null 2>&1 || {
    printf '%s\n' "act is required to run the local workflow." >&2
    exit 2
}
command -v podman >/dev/null 2>&1 || {
    printf '%s\n' "podman is required to run the local workflow." >&2
    exit 2
}
podman info >/dev/null
podman image exists "$IMAGE" || {
    printf '%s\n' "Runner image $IMAGE is not available locally." >&2
    exit 2
}

export DOCKER_HOST=$SOCKET
exec act -W "$ROOT/.github/workflows/test.yml" -j test \
    -P "self-hosted=$IMAGE" \
    --container-architecture linux/amd64 \
    --pull=false
