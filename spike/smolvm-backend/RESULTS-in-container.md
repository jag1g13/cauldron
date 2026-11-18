# smolvm backend spike - results (inside the dev container)

Same tiers as the host run (see RESULTS.md), executed *inside* the
`cauldron-cauldron` dev container on the DGX Spark host: smolvm 1.35.3
installed into the image via post_build, `/dev/kvm` passed with
`devices = ["/dev/kvm"]`, KVM access made durable with
`scripts/kvm-acl-fix.sh` (the stock logind/uaccess machinery erases a named
ACL on /dev/kvm ~1 s after a VM is created; see scripts/README.md). Image
builds go through the host Podman via the mounted remote client (build
contexts were placed on the workspace mount, same path on both sides).

- host: Linux 7.0.0-1019-nvidia aarch64
- binary: `smolvm` (smolvm 1.35.3)
- date: 2026-11-18T21:02:45Z

| status | check |
|--------|-------|
| PASS | smolvm present: smolvm 1.35.3 |
| PASS | /dev/kvm present |
| PASS | host builder: podman |
| PASS | 1a throwaway machine boots (machine run --net --image alpine) |
| PASS | 1b named machine create+start; exec works; bind mount present |
| PASS | 1c init ran once (count stayed 1 across stop+start) |
| PASS | 1d rootfs persists across stop+start |
| PASS | 1e bind mount still visible after restart |
| PASS | 2.1 named machine from docker-save tar boots; marker present in rootfs |
| PASS | 2.1b Smolfile 'image = <tar>' also works |
| PASS | 2.2 exec --interactive forwards stdin |
| PASS | 2.2b interactive read from stdin works |
| PASS | 2.3 numeric uid runs; host bind mount writable by that uid |
| PASS | 2.4 produced a recognizable host-port conflict error |
| PASS | 2.5 captured machine list output (first 30 lines below) |
| PASS | 4.1 /etc/hosts append via root exec works (cauldron known_hosts path) |
| PASS | 3.1 Remote-SSH ProxyCommand through smolvm |
| PASS | 3.2 machine exec --user 0 accepted |
