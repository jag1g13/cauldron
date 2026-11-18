#!/usr/bin/env bash
#
# diag-smolvm-build.sh — investigate the smolvm backend's host-builder step.
#
# Targets the symptom:
#   Error: docker-archive doesn't support modifying existing images
# on `cauldron up --build` (the podman/docker build+save step, not smolvm).
#
# Usage:
#   diag-smolvm-build.sh             read-only diagnostics
#   diag-smolvm-build.sh --retry     diagnostics, then a clean rebuild
#
# Overrides:
#   CONTAINER=cauldron-smoltest            machine/image name to inspect
#   CAULDRON_PROJECT=$HOME/tools/cauldron  repo to invoke `cauldron` from
#   TAR_DIR=$HOME/.cache/cauldron/smoltar  where built image tars are cached

set -uo pipefail

CONTAINER="${CONTAINER:-cauldron-smoltest}"
CAULDRON_PROJECT="${CAULDRON_PROJECT:-$HOME/tools/cauldron}"
TAR_DIR="${TAR_DIR:-$HOME/.cache/cauldron/smoltar}"
RETRY=0
[ "${1:-}" = "--retry" ] && RETRY=1

hr() { printf '\n\x1b[1m== %s ==\x1b[0m\n' "$1"; }

if ! command -v podman >/dev/null; then
    echo "podman not found on PATH (the host builder for the smolvm backend)."
    exit 1
fi

hr "podman version + storage"
podman version 2>&1 | sed -n '1,3p'
printf 'storage driver : %s\n' "$(podman info --format '{{.Store.GraphDriverName}}' 2>&1)"
printf 'graph root     : %s\n' "$(podman info --format '{{.Store.GraphRoot}}' 2>&1)"
echo "note           : overlay/vfs expected; docker-archive is the likely culprit"

hr "smolvm tar cache ($TAR_DIR)"
ls -la "$TAR_DIR" 2>&1 || echo "(no tar dir yet)"

hr "cauldron images in the store"
podman image ls --format '{{.Repository}}:{{.Tag}}  {{.ID}}  {{.Size}}' 2>&1 \
    | grep -E "cauldron|<none>" || echo "(none)"

hr "isolate the SAVE step  (podman save <image> -> /tmp tar)"
if podman image inspect "$CONTAINER:latest" >/dev/null 2>&1; then
    if podman save -o /tmp/smoltest-save.tar "$CONTAINER:latest" 2> /tmp/smoltest-save.err; then
        echo "save OK"
        ls -la /tmp/smoltest-save.tar
    else
        echo "save FAILED:"
        cat /tmp/smoltest-save.err
    fi
else
    echo "image $CONTAINER:latest not present; skipping isolated save test"
fi

[ "$RETRY" -ne 1 ] && { hr "diagnostics complete (add --retry to attempt a clean rebuild)"; exit 0; }

hr "CLEAN RETRY"
echo "Will remove:"
echo "  - $TAR_DIR/*.tar"
echo "  - podman images: $CONTAINER:latest, $CONTAINER-intermediate:latest"
printf 'Proceed? [y/N] '
read -r ans
case "$ans" in y | Y) ;; *) echo "aborted"; exit 0 ;; esac

rm -f "$TAR_DIR"/*.tar
podman image rm "$CONTAINER:latest" "$CONTAINER-intermediate:latest" 2>/dev/null

if ! command -v uv >/dev/null; then
    echo "uv not found; run 'cauldron up --build' manually from $CAULDRON_PROJECT."
    exit 1
fi
uv run --isolated --project "$CAULDRON_PROJECT" cauldron up --build
