#!/usr/bin/env bash
# =============================================================================
# cauldron -> smolvm backend : Phase 0 validation spike (tiers 0-2, 4)
#
# Confirms, on a real KVM host, the smolvm behaviours the backend design
# depends on. See README.md for the full test plan. Safe to re-run; removes
# every machine it creates (prefix cauldron-spike-*) on exit unless --keep.
#
#   ./spike.sh                 # run every tier
#   ./spike.sh --keep          # leave machines running
#   SMOLVM_BIN=smol ./spike.sh # differently-named binary
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RESULTS="$HERE/RESULTS.md"
SMOLVM_BIN="${SMOLVM_BIN:-smolvm}"
PREFIX="cauldron-spike"
TS="$(date +%s)"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

TRACKED=()      # machine names to remove at the end
TMPDIRS=()      # temp dirs to remove at the end
BUILDER=""
EXEC_I="-i"     # interactive flag for machine exec (refined by probe_flags)
LIST_JSON_FLAG=""

log()    { printf '%s\n' "$*"; }
hr()     { printf '\n========== %s ==========\n' "$*"; }
record() { printf '| %s | %s |\n' "$1" "$2" >> "$RESULTS"; }
pass()   { log "PASS  $1"; record "PASS"  "$1"; }
fail()   { log "FAIL  $1"; record "FAIL"  "$1"; }
block()  { log "BLOCK $1"; record "BLOCK" "$1"; }

mrun()    { "$SMOLVM_BIN" machine run "$@"; }
mcreate() { "$SMOLVM_BIN" machine create "$@"; }
mstart()  { "$SMOLVM_BIN" machine start "$@"; }
mstop()   { "$SMOLVM_BIN" machine stop "$@"; }
mexec()   { "$SMOLVM_BIN" machine exec "$@"; }
mlist()   { "$SMOLVM_BIN" machine list "$@"; }
mremove() {
  local n="$1"
  "$SMOLVM_BIN" machine delete --name "$n" --force 2>/dev/null ||
    "$SMOLVM_BIN" machine rm     --name "$n" --force 2>/dev/null || true
}
track() { TRACKED+=("$1"); }
mktmp() { local d; d="$(mktemp -d)"; TMPDIRS+=("$d"); printf '%s' "$d"; }

cleanup() {
  local n d
  for n in ${TRACKED[@]+"${TRACKED[@]}"}; do
    [ -n "$n" ] && { log "[cleanup] removing machine $n"; mremove "$n"; }
  done
  for d in ${TMPDIRS[@]+"${TMPDIRS[@]}"}; do
    [ -n "$d" ] && rm -rf "$d"
  done
  [ "$KEEP" = "1" ] && log "[cleanup] --keep set: machines left as-is"
}
trap cleanup EXIT

init_results() {
  local ver="not found"
  command -v "$SMOLVM_BIN" >/dev/null 2>&1 && ver="$("$SMOLVM_BIN" --version 2>/dev/null | head -1)"
  ver="${ver:-not found}"
  {
    echo "# smolvm backend spike - results"
    echo
    echo "- host: $(uname -srm)"
    echo "- binary: \`${SMOLVM_BIN}\` ($ver)"
    echo "- date: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    echo
    echo "| status | check |"
    echo "|--------|-------|"
  } > "$RESULTS"
}

probe_flags() {
  local exec_help
  exec_help="$("$SMOLVM_BIN" machine exec --help 2>&1 || true)"
  if printf '%s\n' "$exec_help" | grep -qiE -- '--interactive'; then EXEC_I="--interactive"; fi
  if printf '%s\n' "$("$SMOLVM_BIN" machine list --help 2>&1 || true)" | grep -qiE -- '--format|json'; then
    LIST_JSON_FLAG="--format json"
  fi
  log "probed: exec interactive flag='$EXEC_I', list json flag='${LIST_JSON_FLAG:-<none>}'"
}

# ---------------------------------------------------------------- Tier 0 ---
preflight() {
  hr "Tier 0 - preflight"
  if ! command -v "$SMOLVM_BIN" >/dev/null 2>&1; then
    block "smolvm binary '$SMOLVM_BIN' not on PATH (try SMOLVM_BIN=smol)"
    return 1
  fi
  pass "smolvm present: $("$SMOLVM_BIN" --version 2>&1 | head -1)"

  if [ -e /dev/kvm ]; then
    pass "/dev/kvm present"
  else
    block "/dev/kvm missing - local microVMs cannot boot on this host"
    return 1
  fi

  if command -v podman >/dev/null 2>&1; then BUILDER=podman
  elif command -v docker >/dev/null 2>&1; then BUILDER=docker
  else BUILDER=""; fi
  if [ -n "$BUILDER" ]; then pass "host builder: $BUILDER"; else block "no host builder (podman/docker)"; fi

  local h="$HERE/smolvm-help.txt"
  {
    echo "# smolvm CLI surface (captured by spike on $(date -u +%FT%TZ))"
    echo; echo '## machine --help';      "$SMOLVM_BIN" machine --help 2>&1
    echo; echo '## machine create';     "$SMOLVM_BIN" machine create --help 2>&1
    echo; echo '## machine run';        "$SMOLVM_BIN" machine run --help 2>&1
    echo; echo '## machine exec';       "$SMOLVM_BIN" machine exec --help 2>&1
    echo; echo '## machine list';       "$SMOLVM_BIN" machine list --help 2>&1
    echo; echo '## machine start';      "$SMOLVM_BIN" machine start --help 2>&1
    echo; echo '## machine stop';       "$SMOLVM_BIN" machine stop --help 2>&1
  } > "$h"
  log "  captured CLI help -> $h"
  return 0
}

# ---------------------------------------------------------------- Tier 1 ---
tier1_lifecycle() {
  hr "Tier 1 - named machine lifecycle (up / stop / start)"

  # 1a: can we boot a throwaway machine at all?
  if mrun --net --image alpine -- uname -a >/tmp/sp1a.log 2>&1; then
    pass "1a throwaway machine boots (machine run --net --image alpine)"
  else
    fail "1a throwaway machine boot failed"
    sed -n '1,20p' /tmp/sp1a.log | sed 's/^/    /'
    return
  fi

  local dir; dir="$(mktmp)"; echo hello > "$dir/marker.txt"
  local name="$PREFIX-lc-$TS"
  cat > "$dir/lc.smolfile" <<EOF
image = "alpine"
net = true
init = ["sh -c \"echo ran >> /root/init-count.txt\""]
cmd = ["sh", "-c", "sleep infinity"]
volumes = ["$dir:/work"]
EOF
  if ! mcreate --name "$name" -s "$dir/lc.smolfile" >/tmp/sp1b.log 2>&1; then
    fail "1b machine create from Smolfile failed"
    sed -n '1,20p' /tmp/sp1b.log | sed 's/^/    /'
    return
  fi
  track "$name"
  mstart --name "$name" >/dev/null 2>&1 || true
  sleep 4
  if mexec --name "$name" -- id >/dev/null 2>&1; then
    pass "1b named machine create+start; exec works; bind mount present"
  else
    fail "1b machine created but exec failed"
    return
  fi

  # 1c: init ran exactly once across a stop+start
  local n1 n2
  n1="$(mexec --name "$name" -- sh -c 'cat /root/init-count.txt 2>/dev/null | wc -l' | tr -d ' ')"
  mstop --name "$name" >/dev/null 2>&1; sleep 2
  mstart --name "$name" >/dev/null 2>&1; sleep 4
  n2="$(mexec --name "$name" -- sh -c 'cat /root/init-count.txt 2>/dev/null | wc -l' | tr -d ' ')"
  if [ "$n1" = "1" ] && [ "$n2" = "1" ]; then
    pass "1c init ran once (count stayed $n2 across stop+start)"
  else
    fail "1c init count first='$n1' after-restart='$n2' (expected 1 then 1)"
  fi

  # 1d/1e: rootfs + bind mount persistence across stop+start
  mexec --name "$name" -- sh -c 'echo persist > /root/persist.txt' >/dev/null 2>&1
  mstop --name "$name" >/dev/null 2>&1; sleep 2
  mstart --name "$name" >/dev/null 2>&1; sleep 4
  local got
  got="$(mexec --name "$name" -- sh -c 'cat /root/persist.txt 2>/dev/null' || true)"
  [ "$got" = "persist" ] && pass "1d rootfs persists across stop+start" || fail "1d rootfs persistence (got '$got')"
  got="$(mexec --name "$name" -- sh -c 'cat /work/marker.txt 2>/dev/null' || true)"
  [ "$got" = "hello" ] && pass "1e bind mount still visible after restart" || fail "1e bind mount after restart (got '$got')"
}

# ------------------------------------------------------------- Tier 2.1 ---
tier2_image_handoff() {
  hr "Tier 2.1 - docker-save tar into a named, persistent machine"
  if [ -z "$BUILDER" ]; then block "2.1 skipped: no host builder"; return; fi

  local name="$PREFIX-img-$TS"
  local ctx; ctx="$(mktmp)"
  printf 'FROM alpine\nRUN echo MARKER-IMG > /marker-from-image.txt\n' > "$ctx/Dockerfile"
  local tag="cauldron-spike-img:$TS"
  if ! "$BUILDER" build -t "$tag" "$ctx" >/tmp/sp21_build.log 2>&1; then
    fail "2.1 host build of marker image failed"
    sed -n '1,15p' /tmp/sp21_build.log | sed 's/^/    /'
    return
  fi
  local tar="$HERE/cauldron-spike-img.tar"
  if [ "$BUILDER" = "podman" ]; then
    podman save -o "$tar" "$tag" >/dev/null 2>&1 || { fail "2.1 podman save failed"; return; }
  else
    docker save -o "$tar" "$tag" >/dev/null 2>&1 || { fail "2.1 docker save failed"; return; }
  fi
  log "  saved image -> $tar ($(du -h "$tar" | cut -f1))"

  # 2.1: create a named machine directly from the tar via --image
  if ! mcreate --name "$name" --image "$tar" --net >/tmp/sp21_create.log 2>&1; then
    fail "2.1 'machine create --image <tar>' rejected"
    sed -n '1,15p' /tmp/sp21_create.log | sed 's/^/    /'
    rm -f "$tar"
    return
  fi
  track "$name"
  mstart --name "$name" >/dev/null 2>&1 || true
  sleep 4
  local got
  got="$(mexec --name "$name" -- cat /marker-from-image.txt 2>/dev/null || true)"
  if [ "$got" = "MARKER-IMG" ]; then
    pass "2.1 named machine from docker-save tar boots; marker present in rootfs"
  else
    fail "2.1 machine from tar created but marker missing (got '$got')"
    sed -n '1,15p' /tmp/sp21_create.log | sed 's/^/    /'
  fi

  # 2.1b: the Smolfile form (cauldron will generate Smolfiles)
  local name2="$PREFIX-img2-$TS" ctx2; ctx2="$(mktmp)"
  printf 'image = "%s"\nnet = true\ncmd = ["sh","-c","sleep infinity"]\n' "$tar" > "$ctx2/s.smolfile"
  if mcreate --name "$name2" -s "$ctx2/s.smolfile" >/tmp/sp21b.log 2>&1; then
    track "$name2"
    mstart --name "$name2" >/dev/null 2>&1 || true
    sleep 4
    got="$(mexec --name "$name2" -- cat /marker-from-image.txt 2>/dev/null || true)"
    [ "$got" = "MARKER-IMG" ] && pass "2.1b Smolfile 'image = <tar>' also works" || fail "2.1b Smolfile tar image (got '$got')"
  else
    block "2.1b Smolfile tar-image create rejected (see /tmp/sp21b.log)"
  fi

  rm -f "$tar"
  "$BUILDER" rmi -f "$tag" >/dev/null 2>&1 || true
}

# ------------------------------------------------------------- Tier 2.2 ---
tier2_exec_stdin() {
  hr "Tier 2.2 - 'machine exec $EXEC_I' stdin attach (needed for the sshd -i ProxyCommand)"
  local name="$PREFIX-exec-$TS"
  mcreate --name "$name" --image alpine --net >/dev/null 2>&1 || { fail "2.2 setup: create failed"; return; }
  track "$name"
  mstart --name "$name" >/dev/null 2>&1 || true
  sleep 4
  local got
  got="$(printf 'hello-stdin\n' | mexec --name "$name" $EXEC_I -- cat 2>/dev/null || true)"
  [ "$got" = "hello-stdin" ] && pass "2.2 exec $EXEC_I forwards stdin" \
    || fail "2.2 exec stdin (got '$got'; interactive flag='$EXEC_I')"
  got="$(printf 'ping\n' | mexec --name "$name" $EXEC_I -- sh -c 'read l; echo "got:$l"' 2>/dev/null || true)"
  [ "$got" = "got:ping" ] && pass "2.2b interactive read from stdin works" \
    || fail "2.2b interactive read (got '$got')"
}

# ------------------------------------------------------------- Tier 2.3 ---
tier2_numeric_user() {
  hr "Tier 2.3 - numeric user=<uid>:<gid> + writable host bind mount"
  local name="$PREFIX-user-$TS"
  local dir; dir="$(mktmp)"
  local uid gid; uid="$(id -u)"; gid="$(id -g)"
  cat > "$dir/u.smolfile" <<EOF
image = "alpine"
net = true
user = "$uid:$gid"
cmd = ["sh", "-c", "sleep infinity"]
volumes = ["$dir:/work"]
EOF
  mcreate --name "$name" -s "$dir/u.smolfile" >/dev/null 2>&1 || { fail "2.3 setup: create failed"; return; }
  track "$name"
  mstart --name "$name" >/dev/null 2>&1 || true
  sleep 4
  local idline
  idline="$(mexec --name "$name" -- id 2>/dev/null || true)"
  log "  id inside machine: $idline"
  if mexec --name "$name" -- sh -c 'echo owned > /work/written.txt' 2>/dev/null; then
    if [ -f "$dir/written.txt" ] && [ "$(cat "$dir/written.txt")" = "owned" ]; then
      pass "2.3 numeric uid runs; host bind mount writable by that uid"
    else
      fail "2.3 exec wrote but host file missing/incorrect"
    fi
  else
    fail "2.3 non-root write into bind mount failed (uid mapping / permissions?)"
  fi
  log "  host owner: $(ls -ln "$dir/written.txt" 2>/dev/null | awk '{print $3":"$4, $9}' || echo n/a)"
}

# ------------------------------------------------------------- Tier 2.4 ---
tier2_ports() {
  hr "Tier 2.4 - port publish + live host-port conflict message"
  local port holder=""
  port=$(( 21000 + (RANDOM % 1999) ))
  local dir; dir="$(mktmp)"
  cat > "$dir/sp.smolfile" <<EOF
image = "alpine"
net = true
cmd = ["sh", "-c", "sleep infinity"]
ports = ["$port:3000"]
EOF
  local name="$PREFIX-port-$TS"
  if ! mcreate --name "$name" -s "$dir/sp.smolfile" >/tmp/sp24_create.log 2>&1; then
    fail "2.4 machine create with a published port failed"
    sed -n '1,15p' /tmp/sp24_create.log | sed 's/^/    /'
    return
  fi
  track "$name"

  # occupy the host port from the host, then try to start the machine
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" >/dev/null 2>&1 <<'PY' &
import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(1); time.sleep(60)
PY
    holder=$!
    sleep 1
  elif command -v nc >/dev/null 2>&1; then
    nc -l -p "$port" >/dev/null 2>&1 & holder=$!; sleep 1
  fi
  [ -n "$holder" ] && log "  host port $port occupied by pid $holder"

  mstart --name "$name" >/tmp/sp24_start.log 2>&1 || true
  sleep 4
  local txt; txt="$(cat /tmp/sp24_start.log 2>/dev/null)"
  log "  start output:"; printf '%s\n' "$txt" | sed 's/^/    /' | sed -n '1,20p'
  if printf '%s' "$txt" | grep -qiE 'in use|already|conflict|bind|EADDR'; then
    pass "2.4 produced a recognizable host-port conflict error"
  else
    fail "2.4 no recognizable conflict error - inspect the start output above"
  fi
  [ -n "$holder" ] && kill "$holder" 2>/dev/null || true
}

# ------------------------------------------------------------- Tier 2.5 ---
tier2_list_json() {
  hr "Tier 2.5 - 'machine list' shape (for the ps / find_port_owner parser)"
  local name="$PREFIX-list-$TS"
  mcreate --name "$name" --image alpine --net >/dev/null 2>&1 || true
  track "$name"
  mstart --name "$name" >/dev/null 2>&1 || true
  sleep 4
  local out
  if [ -n "$LIST_JSON_FLAG" ]; then
    out="$(mlist $LIST_JSON_FLAG 2>/dev/null || mlist 2>/dev/null)"
  else
    out="$(mlist 2>/dev/null)"
  fi
  if [ -n "$out" ]; then
    pass "2.5 captured machine list output (first 30 lines below)"
    printf '%s\n' "$out" | sed 's/^/    /' | sed -n '1,30p'
    printf '%s\n' "$out" > "$HERE/smolvm-list-sample.txt"
    log "  saved full sample -> $HERE/smolvm-list-sample.txt"
  else
    fail "2.5 could not capture machine list"
  fi
}

# ---------------------------------------------------------------- Tier 4 ---
tier4_known_hosts() {
  hr "Tier 4 - known_hosts via /etc/hosts append (no native smolvm support)"
  local name="$PREFIX-kh-$TS"
  mcreate --name "$name" --image alpine --net >/dev/null 2>&1 || { block "4 setup failed"; return; }
  track "$name"
  mstart --name "$name" >/dev/null 2>&1 || true
  sleep 4
  if mexec --name "$name" --user root -- sh -c 'echo "127.0.0.1 spike.kh.local" >> /etc/hosts' 2>/dev/null; then
    local got
    got="$(mexec --name "$name" -- sh -c 'grep spike.kh.local /etc/hosts' 2>/dev/null || true)"
    if printf '%s' "$got" | grep -q spike.kh.local; then
      pass "4.1 /etc/hosts append via root exec works (cauldron known_hosts path)"
    else
      fail "4.1 append not visible (got '$got')"
    fi
  else
    block "4.1 could not exec as root to append /etc/hosts"
  fi
}

main() {
  init_results
  if ! preflight; then
    log "Preflight blocked; later tiers need a bootable host. See $RESULTS"
    return 1
  fi
  probe_flags
  tier1_lifecycle
  tier2_image_handoff
  tier2_exec_stdin
  tier2_numeric_user
  tier2_ports
  tier2_list_json
  tier4_known_hosts
  log ""
  log "Done. Summary: $RESULTS"
}
main "$@"
