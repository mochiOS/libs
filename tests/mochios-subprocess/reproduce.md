# Native GLib GSubprocess verification

This suite runs the real target GLib/GIO on mochiOS. It exercises the ordinary
C runtime `exec` authorization path, including the shared loader's authenticated
reconstruction of the executable and its DSOs. It does not substitute the
AppCore scoped-launch protocol for child `exec`, introduce a service, or grant
additional privileges to make the tests pass.

## SDK candidate and prerequisites

Use an unpublished development SDK candidate containing the updated
`lib/libmochi_user_newlib_runtime.a` (user commit `f9f452b`) and the matching
capability service (`e0d2309`). Published SDK `27.0-dp.1-shared.4` is unchanged and
still contains the old runtime; its version number alone does not identify this
candidate. `build.py` records the actual runtime archive digest, executable and
DSO digests, GLib version and candidate provenance. If `candidate.json` is present,
its recorded runtime digest must match the archive before the fixture is built.

A candidate may reuse the published SDK's compiler, headers and unchanged
libraries, replacing only the executable-owned runtime archive. Keep the source
SDK immutable: use a separate copy, and unlink a hardlinked destination before
replacing an archive. Do not label this a released or fully validated SDK.

Other prerequisites:

- A merged target prefix with GIO/GLib, headers, pkg-config files, SONAME links
  and transitive dependencies. The recorded target uses GLib 2.90.1. Existing
  DSOs import libc symbols from the executable-owned runtime; they do not need
  rebuilding merely to bind the updated `exec` implementation.
- A normal development OS image, already configured for automatic login, and
  `DEVELOPMENT_SYSTEM_SIGNATURES=y`. Neither setting is changed by this suite.
- `msign`, pkg-config, QEMU, development Secure Boot OVMF firmware, debugfs,
  mtools and the existing OS boot-module packing tools. No network service or
  external server is needed. KVM is optional; TCG is supported.

From the libs checkout:

```sh
python3 tests/mochios-subprocess/build.py \
  --os-root "$OS_ROOT" --sdk "$SDK_CANDIDATE" --prefix "$TARGET_PREFIX" \
  --capability-binary "$MATCHING_CAPABILITY_BINARY" \
  --output out/native-subprocess-build
python3 tests/mochios-tls/image.py \
  --os-root "$OS_ROOT" --stage out/native-subprocess-build/stage \
  --output out/native-subprocess-vm
python3 tests/mochios-subprocess/run.py \
  --image-directory out/native-subprocess-vm \
  --build out/native-subprocess-build/build.json
```

Use fresh output directories for every run. The image composer normally reads
`$OS_ROOT/out/artifacts/disk.img`. To isolate testing from concurrent OS builds,
`--base-image-directory` selects a previously composed **standalone** qcow2 with
its `image.json`; backing chains and mismatched virtual sizes are rejected.
`--kernel-directory` selects a matching `kernel.elf`/`kernel.meta` pair.
The source image stays read-only. Only the private image's measured System
content, normal built-in bindings, boot module and declarative session-startup
configuration are updated. Existing account and Capability policy are preserved.

## Cases

The static application entry sets the library/PATH environment and calls ordinary
`execv` on the dynamic GIO client. The client uses the actual `GSubprocess` APIs:

| Case | Required observation |
| --- | --- |
| `pipes-argv-env-cwd` | Exact arguments (including spaces), launcher environment and `/tmp` working directory; stdin reaches the child, stdout and stderr stay separate |
| `nonzero-exit` | Exit 7 is reported as `G_SPAWN_EXIT_ERROR`, rather than success |
| `path-search` | A basename is found through PATH and executes successfully |
| `async-wait` | The main-context callback observes successful child completion |
| `cancelled-wait` | Cancellation returns `G_IO_ERROR_CANCELLED`; it does not kill the child, which subsequently exits 0 on stdin EOF |
| `missing-executable` | A nonexistent target returns `G_SPAWN_ERROR_NOENT` |

The guest writes `/tmp/gsubprocess-results.log`. Every case must appear exactly
once as `PASS`, followed by one `GSUBPROCESS COMPLETE failures=0`. Any failure,
missing case, duplicate result, incomplete log, guest fault or timeout exits
nonzero. A boot-ready message is not a pass. The runner reads read-only copies
of the guest Data partition and saves `result.json` with actual artifact/image
hashes. It terminates its VM when finished.

## Limits and evidence

A Linux reference run of the same test logic is only a fixture control; it is
separate from the mochiOS native result. This suite is not the entire upstream
GLib test suite, a WebKit process sandbox test, or a complete SDK validation.
It does not test arbitrary standalone uninstalled dynamic code or all possible
FD mappings and signal/termination behavior.

Run the repository's host tests separately:

```sh
python3 -m unittest discover -s tests
```

Native results and input digests are recorded in
`tooling/native-subprocess-verification.json` and its adjacent `.log`.
Publication, release tags, SDK locks and the published SDK archive are not
changed by these tests.
