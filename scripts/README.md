# scripts

Host-side helper scripts. Nothing here is part of the cauldron package; these
run on the machine that hosts your containers.

## kvm-acl-fix.sh — durable `/dev/kvm` access inside containers

**The problem.** Running microVMs (the smolvm backend) inside a rootless
container requires `/dev/kvm` to be writable by the container's uid, which
keep-id maps to your host uid. Access by group — the usual route on the host,
via the `kvm` group — cannot work inside a user namespace: the gid is
unmapped there. The only grants that cross the namespace are the "other"
bits or a named ACL:

```sh
sudo setfacl -m u:<uid>:rw /dev/kvm
```

On some hosts (observed: headless Ubuntu 24.04 "DGX Spark", kernel
7.0.0-nvidia, smolvm 1.35.3) that ACL is silently erased ~1 second after
*any successful VM creation*, and the next VM start fails with
`openat("/dev/kvm") = EACCES`:

1. Creating a KVM VM makes the kernel emit a uevent for the kvm device
   (plain opens, failed opens and the `setfacl` itself do not trigger it —
   only an actual `KVM_CREATE_VM` does).
2. udev re-runs the stock rules: `50-udev-default.rules` reapplies
   `root:kvm 0660`, and `70-uaccess.rules` tags the node `uaccess`.
3. The tag hands the node's ACL to logind, which *replaces* the whole ACL
   with the set of currently seated desktop users — empty on a headless
   host — erasing the named entry.

The ACL therefore looks fine for minutes after applying it and vanishes
exactly when a machine boots, which is what makes the symptom confusing.

**The fix.** For the kvm node only:

- remove the `uaccess` tag, so logind stops rewriting this node's ACL;
- re-apply the named ACL from udev itself on every kvm uevent.

The event that used to erase the grant now restores it. The grant is
identical in scope to the manual `setfacl` above — no other device, no
wider bits, no other users. uevents are emitted only by the kernel's device
model (plus explicit `udevadm trigger`); the rule's `setfacl` writes inode
metadata, which never emits uevents, and never runs KVM ioctls, so the rule
cannot re-trigger itself.

**Use** (on the host, not inside a container):

```sh
sudo bash scripts/kvm-acl-fix.sh
```

**Undo** (one file, nothing else is left behind):

```sh
sudo rm /etc/udev/rules.d/99-kvm-cauldron.rules
sudo udevadm control --reload && sudo udevadm trigger --name-match=kvm
```

**Notes.**

- The rule hard-codes the invoking user's uid at install time (udev runs as
  root and has no notion of "your" uid). Re-run the script to change it.
- Removing `uaccess` affects only this device. On a machine used as a
  graphical console, automatic KVM access for the seated user now comes
  from this rule instead of logind — equivalent on a single-user host.
- `cauldron check` with the smolvm backend verifies the device is writable
  and prints the remedy when it is not.
