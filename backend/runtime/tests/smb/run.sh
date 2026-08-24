#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repository_root="$(CDPATH= cd -- "$script_dir/../../../.." && pwd)"
compose_file="$script_dir/compose.yaml"
project_name="atlanticus-runtime-smb-fencing"
work_root="$(mktemp -d)"
probe_id="local-runtime-fencing"
export ATLANTICUS_REPOSITORY_ROOT="$repository_root"

compose() {
    docker compose -p "$project_name" -f "$compose_file" "$@"
}

cleanup() {
    compose down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$work_root"
}
trap cleanup EXIT INT TERM

run_probe() {
    compose run --rm probe "$@"
}

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required for the runtime SMB fencing harness" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "docker compose is required for the runtime SMB fencing harness" >&2
    exit 1
fi

compose build
compose up -d samba
run_probe prepare --probe-id "$probe_id"

owner_a_log="$work_root/owner-a.log"
owner_b_log="$work_root/owner-b.log"
set +e
compose run --rm probe owner-a --probe-id "$probe_id" >"$owner_a_log" 2>&1 &
owner_a_pid=$!
compose run --rm probe owner-b --probe-id "$probe_id" >"$owner_b_log" 2>&1 &
owner_b_pid=$!
wait "$owner_a_pid"
owner_a_status=$?
wait "$owner_b_pid"
owner_b_status=$?
set -e

if [ "$owner_a_status" -ne 0 ] || [ "$owner_b_status" -ne 0 ]; then
    cat "$owner_a_log" >&2
    cat "$owner_b_log" >&2
    echo "runtime SMB fencing workers failed: owner-a=$owner_a_status owner-b=$owner_b_status" >&2
    exit 10
fi

run_probe verify --probe-id "$probe_id"
