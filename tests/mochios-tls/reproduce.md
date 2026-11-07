# Native GIO TLS / libsoup integration

These tests execute the pinned SDK's C client and the real target DSOs on
mochiOS. Linux reference tests and compile/link checks are separate evidence.
A successful desktop boot alone never passes this suite.

## Prerequisites

- A normally built mochiOS development ESP/System/Data image at
  `<os>/out/artifacts/disk.img`, with existing automatic login enabled.
  The tools require `DEVELOPMENT_SYSTEM_SIGNATURES=y`; they never enable it,
  change account authentication, add a trusted CA, or change Capability policy.
- Current core and drivers, including the interrupt-safe physical-offset read
  and virtio network signed-status fix (`07cf6f2`). Rebuild the regular image
  so its normal built-in verification records bind those exact executables.
- The OS Rust sysroot/target build cache, native linker and msign build tools.
  `build.py` uses the existing OS toolchain definition and offline Cargo cache.
- The verified pinned shared.4 SDK in `out/sdks/<sdk.lock.toml sha256>`.
- A merged target prefix containing libsoup, libgiomochios and all dependencies,
  including headers, development SONAME links and pkg-config metadata.
- Python 3, Make, pkg-config, QEMU, OVMF Secure Boot development firmware,
  e2fsprogs (`debugfs`), mtools, util-linux (`sfdisk`), and an HTTPS connection
  to example.com. KVM is optional; the runner falls back to deterministic TCG.

Build the fixture from the libs repository:

```sh
python3 tests/mochios-tls/build.py \
  --os-root /path/to/mochiOS \
  --prefix /path/to/merged-target-prefix \
  --output out/native-test-build
python3 tests/mochios-tls/image.py \
  --os-root /path/to/mochiOS \
  --stage out/native-test-build/stage \
  --output out/native-test-vm
python3 tests/mochios-tls/run.py \
  --os-root /path/to/mochiOS \
  --image-directory out/native-test-vm \
  --build out/native-test-build/build.json
```

Use fresh output directories. The image composer checks that the source image
remains unchanged, updates only the private test image, and produces a standalone
compressed qcow2 without a mutable backing image. The normal System digest and
boot-module tools bind the modified image; Secure Boot verification stays on.
`--kernel-directory` can select a matching development `kernel.elf`/`kernel.meta`
pair when explicitly testing a rebuilt kernel with an existing System image.

The test app and all its DSOs have normal built-in manifests and measured code
bindings. Only the private image's declarative session startup policy changes.
The small Rust launcher uses the same scoped application launch protocol as
AppCore/Binder, preserving user identity, read scopes, package graph checks and
normal manifest Capability checks. It does not introduce an OS service.
The SDK C `execv` path rejected a prepared dynamic image during this investigation;
that path is not claimed to pass by this fixture.

## Cases and results

The runner starts two host-loopback-only fixtures on the ports recorded in
`build.json`. Defaults are 32895 and 45329; override both at build time if needed.
The TLS fixture uses the OS repository's existing self-signed test certificate,
which is never installed in guest trust. The stalled fixture withholds ServerHello.
The guest waits for an actual connection to a fixture before testing TLS.

1. HTTPS to example.com: status 200, nonempty content and TLS 1.3 are required.
   The observed HTTP version is logged (the recorded run negotiated HTTP/2).
2. Self-signed certificate: handshake must fail with `G_TLS_ERROR_BAD_CERTIFICATE`.
3. Async cancellation: handshake must complete with `G_IO_ERROR_CANCELLED`.

`native-tls-results.log` is read from a read-only copy of the guest Data partition.
`result.json` records every case, the SDK hash and source/System/kernel image
hashes. Missing cases, duplicate completion markers, any failure or timeout exit
nonzero. The VM and host listeners stop on completion or error. Fresh images
are required for reruns, so an old success log cannot satisfy a new run.

These fixtures and VM outputs are development tests, not publication artifacts.
They do not claim the complete upstream test suites, all SDK APIs or the full
pinned production-container dependency graph passed.
