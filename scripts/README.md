# scripts

Environment helper scripts. Nothing here is part of the cauldron package;
each script says where it runs (host vs. inside a VM).

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

## docker-setup.sh — rootful Docker inside a smolvm guest

**Why rootful and not rootless.** Inside a single-user dev VM the microVM
boundary is already the isolation layer, so rootful Docker is the simpler
fit: it drops the rootlesskit wrapper/proxy, user-namespace and subuid
plumbing, slirp4netns, the per-boot `/dev/net/tun` permission dance, and
the cgroup-v2 delegation tree, and uses plain kernel bridge networking.
The trade-off is that containers run as root *inside the VM*.

**The problem.** Running Docker *inside* a smolvm VM (e.g. to test
cauldron-built images) breaks the stock setup in two ways:

1. **No systemd.** PID 1 is smolvm's minimal init, so the units the
   `docker.io` postinst installs never run and nothing starts the daemon.
2. **The root filesystem is itself an overlay** (lower = packed image
   layers, upper = the persistent per-VM overlay). The kernel rejects
   overlay-on-overlay — `mount: overlay: filesystem on ... not supported
   as upperdir` — regardless of privileges, so the kernel's overlay2
   storage driver is unusable and the daemon must use `fuse-overlayfs`.

**The fix.** One script, run as root inside the VM, that installs
`docker.io` + `docker-compose` + `fuse-overlayfs`, pins the storage driver
when the kernel's overlay is unusable (probed once at install), adds the
target user to the `docker` group, and installs two small root helpers
(`docker-start` / `docker-stop`) for the per-boot work an init system would
normally do:

- `docker-start` launches `dockerd` detached (`setsid`, so it survives the
  invoking terminal; log at `/var/log/dockerd.log`; pidfile via
  `--pidfile=/run/docker.pid`) and waits for the socket; idempotent.
- `docker-stop` sends SIGTERM to the pidfile, waits, then SIGKILL.

**Use** (inside the VM, as root, e.g. via a root attach):

```sh
bash scripts/docker-setup.sh            # defaults to user cauldron
bash scripts/docker-setup.sh cauldron --skip-test
```

It ends with a live check (`docker run --rm hello-world`, `docker compose
version`). The target user needs no shell setup: membership in the
`docker` group is enough for the CLI.

After every (re)boot, as root:

```sh
/usr/local/bin/docker-start    # idempotent; waits up to 30 s for the socket
/usr/local/bin/docker-stop
```

The daemon log is at `/var/log/dockerd.log`; the start helper tails it if
the socket never appears.

**Undo:**

```sh
/usr/local/bin/docker-stop
rm /usr/local/bin/docker-start /usr/local/bin/docker-stop
apt-get remove docker.io docker-compose fuse-overlayfs
rm -rf /var/lib/docker /etc/docker/daemon.json
gpasswd -d <user> docker
```

**Notes.**

- Image data lives in `/var/lib/docker`, on the VM's persistent upper
  layer, so images survive reboots — but the daemon itself does not
  auto-start; run the start helper.
- fuse-overlayfs is slower than the kernel driver; for a dev VM that is a
  fair trade. If the data root is ever moved to a non-overlay mount, the
  `storage-driver` pin in `daemon.json` can be dropped.
- Container networking is the stock kernel bridge (veth + iptables); it
  needs no kernel module loading or device-permission fixes. TSI's netns
  machinery inside the VM is a separate concern; treat any DNS/UDP oddity
  in containers as its own bug.
