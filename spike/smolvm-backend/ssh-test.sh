#!/usr/bin/env bash
# =============================================================================
# cauldron -> smolvm backend : Phase 0 spike, Tier 3 (Remote-SSH end-to-end)
#
# Validates the exact mechanism `cauldron code` relies on: sshd running inside
# the machine, reached through an SSH ProxyCommand of
#     smolvm machine exec --user root -i <name> /usr/sbin/sshd -i -o ...
#
# Self-diagnosing: everything useful is written to ssh-debug.txt next to this
# script (host state, sshd -t, key/config dump, a raw ProxyCommand run, and a
# full `ssh -vvv` trace). Send that file back when a check fails.
#
#   ./ssh-test.sh              # run the check (cleans up on exit)
#   ./ssh-test.sh --keep       # leave the machine running
#   SMOLVM_BIN=smol ./ssh-test.sh
#
# Needs: smolvm, /dev/kvm, network (pulls debian:trixie-slim + apt packages).
# Does NOT need a host builder (sshd is installed at runtime, as cauldron's
# image build would).
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPORT="$HERE/ssh-debug.txt"
SMOLVM_BIN="${SMOLVM_BIN:-smolvm}"
PREFIX="cauldron-spike"
TS="$(date +%s)"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

log()   { printf '%s\n' "$*"; }
dbg()   { printf '%s\n' "$*" | tee -a "$REPORT"; }
sect()  { printf '\n===== %s =====\n' "$*" | tee -a "$REPORT"; }
mcreate() { "$SMOLVM_BIN" machine create "$@"; }
mstart()  { "$SMOLVM_BIN" machine start "$@"; }
mexec()   { "$SMOLVM_BIN" machine exec "$@"; }
mremove() {
  "$SMOLVM_BIN" machine delete --name "$1" 2>/dev/null ||
    "$SMOLVM_BIN" machine rm     --name "$1" 2>/dev/null || true
}
# run a command inside the machine, capturing combined output to the report
inm() { mexec --name "$name" --user root -- "$@" 2>&1 | tee -a "$REPORT"; }

: > "$REPORT"
name="$PREFIX-ssh-$TS"
work="$(mktemp -d)"
cleanup() {
  [ "$KEEP" = "1" ] && { log "[cleanup] --keep: leaving $name running"; return; }
  log "[cleanup] removing $name"; mremove "$name"
  rm -rf "$work"
}
trap cleanup EXIT

if ! command -v "$SMOLVM_BIN" >/dev/null 2>&1; then
  log "BLOCK: smolvm binary '$SMOLVM_BIN' not on PATH"; exit 1
fi
[ -e /dev/kvm ] || { log "BLOCK: /dev/kvm missing"; exit 1; }

dbg "host: $(uname -srm)  smolvm: $("$SMOLVM_BIN" --version 2>&1 | head -1)  date: $(date -u +'%FT%TZ')"
dbg "machine name: $name"

# 1. machine from the default cauldron base image
log "creating $name from debian:trixie-slim ..."
mcreate --name "$name" --image debian:trixie-slim --net >/tmp/sp3_create.log 2>&1 \
  || { log "FAIL 3.0 machine create:"; sed -n '1,12p' /tmp/sp3_create.log | tee -a "$REPORT" | sed 's/^/    /'; exit 1; }
mstart --name "$name" >/dev/null 2>&1 || true
sleep 6

# 2. install openssh-server as root (mirrors cauldron's image build step)
log "installing openssh-server as root ..."
mexec --name "$name" --user root -- sh -c \
  'apt-get update -qq && apt-get install -y -qq openssh-server >/dev/null && mkdir -p /run/sshd' \
  >/tmp/sp3_install.log 2>&1 || log "  (apt install reported an issue)"
sect "install tail (last 8 lines)"
tail -n 8 /tmp/sp3_install.log | tee -a "$REPORT"
if mexec --name "$name" --user root -- test -f /usr/sbin/sshd >/dev/null 2>&1; then
  log "sshd present"
else
  log "FAIL 3.0 no /usr/sbin/sshd in the machine"
  printf '| FAIL | 3.0 sshd not installed in machine |\n' >> "$HERE/RESULTS.md"
  exit 1
fi

# 3. host keypair; install authorized_keys + generate host keys in the machine
key="$work/id_ed25519"
ssh-keygen -t ed25519 -f "$key" -N "" -C cauldron-spike >/dev/null 2>&1 || { log "FAIL: ssh-keygen"; exit 1; }
pub="$(cat "$key.pub")"
mexec --name "$name" --user root -- sh -c \
  "mkdir -p /root/.ssh && printf '%s\n' '$pub' > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys" \
  >/dev/null 2>&1 || log "  WARN: failed to install authorized_keys"
mexec --name "$name" --user root -- ssh-keygen -A >/dev/null 2>&1 || log "  (host-keygen -A reported an issue)"
log "installed authorized_keys + host keys"

# 4. ssh config with the smolvm ProxyCommand
cfg="$work/config"
PROXY="$SMOLVM_BIN machine exec --name $name --user root -i -- /usr/sbin/sshd -i -o UsePAM=no -o PasswordAuthentication=no -o PermitRootLogin=prohibit-password"
cat > "$cfg" <<EOF
Host spike
    HostName spike
    User root
    IdentityFile $key
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    ProxyCommand $PROXY
EOF

# 5. in-machine diagnostics
sect "machine ls (state)"
"$SMOLVM_BIN" machine ls 2>&1 | tee -a "$REPORT"
sect "basic exec: id"
inm id
sect "sshd -t (config test)"
inm /usr/sbin/sshd -t -D 2>&1 || true
# -t alone may fork; run a foreground config test:
mexec --name "$name" --user root -- /usr/sbin/sshd -t 2>&1 | tee -a "$REPORT" || true
sect "host keys"
inm ls -l /etc/ssh/ssh_host_*
sect "permissions: /root /root/.ssh /run/sshd"
inm ls -ld /root /root/.ssh /run/sshd
sect "authorized_keys"
inm cat /root/.ssh/authorized_keys
sect "sshd_config (relevant)"
inm sh -c "grep -Ei 'PermitRootLogin|Port|PasswordAuth|UsePAM|ListenAddress|HostKey' /etc/ssh/sshd_config | grep -v '^\s*#' || echo '(no matching uncommented lines)'"
sect "sshd version"
inm /usr/sbin/sshd -V 2>&1 || inm ssh -V

# 6. raw ProxyCommand run (stdin closed -> sshd exits; capture any startup error)
sect "raw ProxyCommand (timeout 6, stdin=/dev/null)"
timeout 6 $SMOLVM_BIN machine exec --name "$name" --user root -i -- \
  /usr/sbin/sshd -i -o UsePAM=no -o PasswordAuthentication=no -o PermitRootLogin=prohibit-password \
  </dev/null 2>&1 | tee -a "$REPORT" || true

# 7. the real test: ssh -vvv
sect "ssh -vv spike 'id'"
ssh -F "$cfg" -vv -o ConnectTimeout=25 -o BatchMode=yes spike 'id; echo CAULDRON_SSH_OK' \
  >"$work/ssh.out" 2>&1
rc=$?
sed 's/^/  /' "$work/ssh.out" | tee -a "$REPORT"

if [ "$rc" -eq 0 ] && grep -q CAULDRON_SSH_OK "$work/ssh.out"; then
  log "PASS 3.1 Remote-SSH ProxyCommand through smolvm works"
  printf '| PASS | 3.1 Remote-SSH ProxyCommand through smolvm |\n' >> "$HERE/RESULTS.md"
else
  log "FAIL 3.1 ssh via smolvm ProxyCommand (rc=$rc)"
  printf '| FAIL | 3.1 Remote-SSH ProxyCommand through smolvm (rc=%s) |\n' "$rc" >> "$HERE/RESULTS.md"
fi

# 8. bonus: does a numeric root uid work for the ProxyCommand too?
if mexec --name "$name" --user 0 -- true >/dev/null 2>&1; then
  log "PASS 3.2 'machine exec --user 0' accepted (numeric root usable in ProxyCommand)"
  printf '| PASS | 3.2 machine exec --user 0 accepted |\n' >> "$HERE/RESULTS.md"
else
  log "BLOCK 3.2 'machine exec --user 0' not accepted (use --user root instead)"
  printf '| BLOCK | 3.2 machine exec --user 0 not accepted |\n' >> "$HERE/RESULTS.md"
fi

log ""
log "Full diagnostics written to: $REPORT"
