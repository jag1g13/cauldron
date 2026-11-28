# smolvm nesting - no-KVM graceful failure UX results (target #3)

Validation of the missing-`/dev/kvm` error paths (feature commit bb612cd),
executed inside the `cauldron-cauldron` machine (smolvm 1.35.4, guest kernel
6.18.60 aarch64, booted **without** `--nested`, so the guest has no
`/dev/kvm`), with the smolvm backend selected in the local config. This is
the composition the DGX Spark host produces, since `--nested` cannot work
there (see RESULTS-podman-in-smolvm.md, "Note on target #1").

- date: 2026-11-19T08:52:00Z

| status | check |
|--------|-------|
| PASS | `cauldron check`: smolvm-installed step ok, then "KVM device is available and writable" fails with the hint (`/dev/kvm is missing. The smolvm backend needs KVM: enable hardware virtualization in BIOS/UEFI and load the kvm module.`), exit 1, no traceback |
| PASS | `cauldron up`: builds the image (48 s with `build_network = "host"`), machine create succeeds, start fails, clean `Error: Failed to start container cauldron-cauldron.`, exit 1, no traceback, no raw smolvm stderr surfaced, no hang |
| PASS | repeat `cauldron up` with the machine left in `created` state: same clean failure via the start path (`Failed to restart container cauldron-cauldron.`), exit 1 |
| PASS | sweep: `smolvm machine delete --name cauldron-cauldron --force` removes the lingering `created` machine |

Notes:
- The check hint for the missing-device case mentions BIOS/virtualization
  and the kvm module; the writable-but-missing-group case (not exercised
  here) carries the setfacl/kvm-group hint from the same step.
- The build step in `up` runs before the machine start, so the no-KVM
  failure only appears after a successful build; that is expected
  ordering, and the failure itself is prompt and clean.
