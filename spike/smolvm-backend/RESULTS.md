# smolvm backend spike - results

- host: Linux 7.0.0-1019-nvidia aarch64
- binary: `smolvm` (smolvm 1.35.3)
- date: 2026-11-18T08:53:24Z

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
