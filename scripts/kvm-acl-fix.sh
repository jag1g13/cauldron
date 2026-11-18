#!/usr/bin/env bash
# Make in-container /dev/kvm access durable on hosts whose udev+logind
# (uaccess) machinery erases the ACL on every kvm uevent.
# See scripts/README.md for the full problem description and evidence.
#
# Run on the HOST (not inside a container):
#   sudo bash scripts/kvm-acl-fix.sh
#
# Effect: installs /etc/udev/rules.d/99-kvm-cauldron.rules, which strips the
# uaccess tag from the kvm node (so logind stops rewriting its ACL) and
# re-applies u:<uid>:rw on every kvm uevent — the event that used to erase
# the grant now restores it. Grants nothing wider than the manual
# `sudo setfacl -m u:<uid>:rw /dev/kvm` it replaces.
#
# Undo (complete — nothing else is left behind):
#   sudo rm /etc/udev/rules.d/99-kvm-cauldron.rules
#   sudo udevadm control --reload && sudo udevadm trigger --name-match=kvm
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run with sudo (on the host, not inside a container)" >&2
    exit 1
fi

# The invoking user's uid (keep-id maps the container uid to it).
uid="${SUDO_UID:-$(id -u)}"
rule_file=/etc/udev/rules.d/99-kvm-cauldron.rules

echo "== installing $rule_file (grant u:$uid:rw on /dev/kvm)"
{
    printf '# cauldron: keep logind (uaccess) from rewriting this ACL; grant uid %s rw\n' "$uid"
    printf 'SUBSYSTEM=="misc", KERNEL=="kvm", TAG-="uaccess", RUN+="/usr/bin/setfacl -m u:%s:rw /dev/kvm"\n' "$uid"
} > "$rule_file"
chmod 0644 "$rule_file"
cat "$rule_file"

echo "== reloading udev and re-triggering the kvm device"
udevadm control --reload
udevadm trigger --name-match=kvm
sleep 2

echo "== resulting device state"
getfacl /dev/kvm
stat -c '%n %A %U:%G' /dev/kvm

echo
echo "If getfacl shows 'user:<user>:rw-' above, the ACL is now durable."
