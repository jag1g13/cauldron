#!/usr/bin/env bash
# Set up rootful Docker + Compose inside a smolvm guest (Debian 13,
# no systemd, root fs that is itself an overlay).
# See scripts/README.md for the full rationale.
#
# Run inside the VM as root (e.g. via a root smolvm attach):
#   bash scripts/docker-setup.sh [user] [--skip-test]
#   (default user: cauldron; added to the docker group so the CLI works
#    in that user's shell without root)
#
# Effect (persistent):
#   - apt: docker.io, docker-compose, fuse-overlayfs
#   - /etc/docker/daemon.json, only if the kernel's overlay is unusable on
#     this root fs (as it is here): storage-driver fuse-overlayfs
#   - <user> in the docker group
#   - /usr/local/bin/docker-start (root, once per boot, idempotent)
#   - /usr/local/bin/docker-stop  (root)
# It then starts the daemon and, unless --skip-test, verifies with
# `docker run --rm hello-world`.
#
# After every (re)boot, as root:  /usr/local/bin/docker-start
#
# Undo (nothing else is left behind):
#   /usr/local/bin/docker-stop
#   rm /usr/local/bin/docker-start /usr/local/bin/docker-stop
#   apt-get remove docker.io docker-compose fuse-overlayfs
#   rm -rf /var/lib/docker /etc/docker/daemon.json
#   gpasswd -d <user> docker
set -euo pipefail

skip_test=0
user_name=""
for arg in "$@"; do
    case "$arg" in
        --skip-test) skip_test=1 ;;
        -*) echo "error: unknown option: $arg" >&2; exit 1 ;;
        *) user_name="$arg" ;;
    esac
done
user_name="${user_name:-cauldron}"

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root, inside the VM" >&2
    exit 1
fi
if ! id "$user_name" >/dev/null 2>&1; then
    echo "error: no such user: $user_name" >&2
    exit 1
fi
if ! command -v apt-get >/dev/null; then
    echo "error: expected a Debian/apt system" >&2
    exit 1
fi

# Postinst hooks must not try to start the daemon: there is no systemd here.
# policy-rc.d gates whether maintainer scripts start services on install. If
# one already exists, preserve it and restore it on exit; otherwise remove
# ours. The EXIT trap covers both a clean finish and an interrupted install
# (set -e), so a stray guard never blocks later service starts.
policy_rcd=/usr/sbin/policy-rc.d
had_policy_rcd=0
if [ -e "$policy_rcd" ]; then
    had_policy_rcd=1
fi
cleanup_policy_rcd() {
    if [ "$had_policy_rcd" -eq 1 ]; then
        mv -f "$policy_rcd.restore" "$policy_rcd"
    else
        rm -f "$policy_rcd"
    fi
}
trap cleanup_policy_rcd EXIT

echo "== guarding service start during install (policy-rc.d)"
if [ "$had_policy_rcd" -eq 1 ]; then
    cp -a "$policy_rcd" "$policy_rcd.restore"
fi
printf '#!/bin/sh\nexit 101\n' > "$policy_rcd"
chmod 0755 "$policy_rcd"

echo "== installing packages"
apt-get update -qq
apt-get install -y -qq docker.io docker-compose fuse-overlayfs

for bin in dockerd docker fuse-overlayfs; do
    command -v "$bin" >/dev/null 2>&1 || {
        echo "error: $bin not found after install" >&2
        exit 1
    }
done

# The kernel rejects overlay-on-overlay regardless of privileges, so on a
# root fs that is itself an overlay the daemon cannot use the kernel's
# overlay2 driver. Probe once; pin fuse-overlayfs when the probe fails.
echo "== storage driver probe"
probe="$(mktemp -d)"
mkdir -p "$probe/l" "$probe/u" "$probe/w" "$probe/m"
if mount -t overlay overlay \
    -o "lowerdir=$probe/l,upperdir=$probe/u,workdir=$probe/w" "$probe/m" \
    2>/dev/null; then
    umount "$probe/m"
    echo "  kernel overlay works; keeping the default overlay2"
else
    echo "  kernel overlay unusable on this root fs; pinning fuse-overlayfs"
    mkdir -p /etc/docker
    if [ -e /etc/docker/daemon.json ]; then
        echo "  (daemon.json exists, leaving it alone)"
    else
        printf '{\n  "storage-driver": "fuse-overlayfs"\n}\n' > /etc/docker/daemon.json
    fi
fi
rm -rf "$probe"

echo "== adding $user_name to the docker group"
if ! id -nG "$user_name" | tr ' ' '\n' | grep -qx docker; then
    usermod -aG docker "$user_name"
fi

echo "== installing /usr/local/bin/docker-start"
cat > /usr/local/bin/docker-start <<'START'
#!/bin/sh
# Start the Docker daemon. No systemd in this VM, so nothing else will
# start it. Run as root, once per boot; safe to re-run.
set -eu

pidfile=/run/docker.pid
if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "docker already running (pid $(cat "$pidfile"))"
    exit 0
fi

# setsid: detach from the invoking terminal (no init to adopt us into).
setsid /usr/sbin/dockerd --pidfile="$pidfile" \
    >> /var/log/dockerd.log 2>&1 < /dev/null &

for _ in $(seq 1 30); do
    if docker version >/dev/null 2>&1; then
        echo "docker is up (server $(docker version --format '{{.Server.Version}}'))"
        exit 0
    fi
    sleep 1
done

echo "error: docker did not come up; last log lines:" >&2
tail -n 20 /var/log/dockerd.log >&2
exit 1
START
chmod 0755 /usr/local/bin/docker-start

echo "== installing /usr/local/bin/docker-stop"
cat > /usr/local/bin/docker-stop <<'STOP'
#!/bin/sh
# Stop the Docker daemon. Run as root.
set -eu

pidfile=/run/docker.pid
if [ ! -f "$pidfile" ]; then
    echo "error: $pidfile not found; not running?" >&2
    exit 1
fi
pid="$(cat "$pidfile")"
if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile"
    echo "not running (removed stale pidfile)"
    exit 0
fi

kill -TERM "$pid"
for _ in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done
if kill -0 "$pid" 2>/dev/null; then
    echo "still alive, sending SIGKILL"
    kill -KILL "$pid"
fi
rm -f "$pidfile"
echo "docker stopped"
STOP
chmod 0755 /usr/local/bin/docker-stop

echo "== starting the daemon"
/usr/local/bin/docker-start

echo "== compose plugin: $(docker compose version)"

if [ "$skip_test" -ne 1 ]; then
    echo "== verification: docker run --rm hello-world"
    docker run --rm hello-world
fi

echo
echo "done. After a reboot, start the daemon with:"
echo "  /usr/local/bin/docker-start   (as root)"
echo "$user_name is in the docker group, so no shell setup is needed."
