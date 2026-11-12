# Independent mochiOS WebKit engine

`mochiOS/libs` owns WebKit source pinning, platform integration, C ABI, dependency
selection and Actions builds. Terra owns its browser UI and engine adapter.
ViewKit owns native UI composition/input/focus/IME/accessibility. AppCore keeps
its generic application/Capability APIs. The OS build system never downloads or
builds WebKit. A dependency of Terra is not part of Terra's source build.

## Current implemented boundary

- The reviewed WPE source, bootstrap profile, archive checks and build driver
  have moved from Terra to `engines/webkit` and `tools/webkit.py`.
- `include/mochios/webkit.h` defines the **proposed**, not yet released, major-1
  C ABI. `src/abi.c` implements version/size negotiation and rejects unavailable
  or malformed backends. No WPE/C++ object layout crosses the ABI.
- C and C++ contract tests exercise negotiation, missing operations, buffer
  overflow checks, invalid display scale and RGBA frame lifetime contracts.
  Test backends are linked only into test executables, never a distributed
  `libwebkit.so`.
- A production configure requires an actual mochiOS SDK, real WPE and reviewed
  backend sources. None are replaced with dummy successful implementations.
  In particular, an empty backend list cannot produce a shared engine library.
- The ordinary library workflow checks the C ABI contract. WebKit remains a
  disabled registry entry until its upstream recipe, backend source and helper
  Capability declarations are implemented. A skipped build is not a built engine.

The initial native backend is implemented in `src/wpe_backend.cpp`. It creates
real headless WPE views, drives their owning GLib context without waiting, and
copies completed WebKitImage snapshots from premultiplied BGRA to immutable RGBA
frame leases. Context shutdown refuses live views, leases and pending snapshots.
WebKit owns one UI RunLoop per process, so a second session is explicitly refused.
Navigation, Back/Forward, Reload/Stop, zoom, pointer/scroll, basic keys and committed
UTF-8 text are connected. Full IME composition and page accessibility are not yet
implemented or advertised. The initial frame bridge uses logical pixel sizes
(scale 1); high-DPI native scale forwarding remains a follow-up.

`MWK_COMMAND_LOAD_HTML` accepts trusted host HTML under an opaque about:blank
origin. It grants no filesystem access. Terra supplies its local engine-check
page; the engine contains no Terra-specific UI or document.

The ordinary disabled `webkit` recipe now builds this small adapter against the
independently built `wpewebkit` prefix through the existing libraries pipeline.
Production remains disabled: helper Capability declarations, final native links,
and Terra process/rendering tests are not yet verified. Build and ABI tests do
not establish successful native browsing. `port.json` retains these blockers.

On mochiOS, Epoxy declares Mesa EGL as an ordinary startup-linked dependency.
Mesa's initial-exec TLS is therefore installed with the authenticated initial
ELF graph. EGL and GLES entry points are resolved through that real provider;
no late TLS support or authentication bypass is implied by this port.

JavaScriptCore's stack-bound prerequisite is available in the reviewed,
unpublished shared.5 SDK candidate. `pthread_getattr_np` uses the kernel's
self-only initial-stack query and the runtime's actual worker-stack/guard
metadata. `tooling/pthread-stack-verification.json` records a native initial and
guarded-worker test using the matching SDK and kernel. This verifies that API,
not JavaScriptCore or WebKit execution.

## Initial upstream port profile

The first upstream WPE verification targets HTML/CSS and interpreted JavaScript
with headless WPEPlatform and the real Mesa software EGL provider. JIT,
WebAssembly, media pipelines and WebGL are disabled in this development profile.
This is not the final Terra feature policy. In particular, upstream ANGLE's WPE
build assumes Linux process/library discovery and driver integration; those
native integrations must be ported and tested before enabling WebGL. It must not
be advertised as working merely because the software EGL prerequisite passed.

The native process memory sampler uses the caller-only
`mochios_process_memory_read` C SDK contract. It reads current resident bytes from
the kernel, not the historical `getrusage().ru_maxrss` peak. No Linux procfs or
RealtimeKit service is assumed. An unavailable kernel/SDK contract fails the
port rather than reporting a fabricated zero memory footprint. The matching
shared.6 SDK is an unpublished development candidate, not an automatic release.

The initial port does not provide a per-page `mincore` residency query. JSC uses
its existing unsupported-platform fallback for the optional paged-out-heap GC
bailout heuristic; it does not fabricate page residency from process totals.
Normal heap marking, sweeping and the real process memory sampler remain in use.

Native bytecode caching is disabled until a stable loaded-image build identity is
available. JSC compiles the existing non-ELF build-version fallback instead of
including GNU `link.h` or calling unavailable `dladdr` / `dl_iterate_phdr`.
This does not substitute the timestamp fallback for an authenticated build ID;
the initial port neither persists nor reuses bytecode under that fallback key.

The shared.6 SDK does not export descriptor-owned `flock` locking. Native WPE
rejects a requested file lock with `ENOTSUP` before opening the path, including
before any create/truncate side effect. Adopting a descriptor with a required
lock also closes and invalidates it on refusal, preserving the lock error.
Ordinary opens without lock requests are unchanged. Required locking is never
silently disabled or mapped to process-owned `fcntl` locks with different close
semantics. Lock-dependent persistence is unsupported until a real native locking
contract is implemented and tested; this remains a development port.

## Runtime package and ABI ownership

The runtime package must update together: `libwebkit.so.1`, its upstream WPE and
JavaScriptCore DSOs, WebContent/network helpers, resources and license notices.
Keep upstream SONAMEs intact. `libwebkit.so` is only the development linker name;
ABI-incompatible changes require a new major and SONAME. Each executable and DSO
needs its normal package binary declaration and authenticated dependency graph.
Helper capabilities are explicit and must be reviewed; there are no default broad
grants or new privileged WebKit service. The engine uses existing native Process,
IPC, delegated filesystem/network scopes and package verification mechanisms.

The small C entry point returns an append-only function table. Contexts and views
belong to their UI thread and application session. Commands copy their arguments
and queue work. Navigation IDs survive redirects, cancellation and late events.
Wakeups schedule bounded nonblocking dispatch on the owning thread. Command
acceptance is not load completion. A missing engine/frame remains unavailable.
Unsupported features return explicit errors.

Frame acquisition leases immutable completed premultiplied RGBA pixels. Release
is explicit. Resize, engine dispatch and I/O do not happen during ViewKit paint.
The eventual ViewKit WebView adapter must translate normal ViewKit pointer/key,
focus and IME events to the C ABI and preserve standard accessibility/clipboard
contracts. This adapter cannot be responsibly connected until native runtime tests establish that the WPE platform
backend renders and processes those events; no placeholder component
is advertised as a working WebView. Browser-specific tabs/history/download UI
remain outside ViewKit and AppCore.

## Build and diagnostics

From the libs repository:

```sh
python3 tools/webkit.py fetch --network
python3 tools/webkit.py prepare
python3 tools/webkit.py doctor --target mochios \
  --deps-prefix /path/to/target-prefix --toolchain /path/to/sdk/share/cmake/mochios.cmake
# Once preflight and the native port succeed, use configure, build and install
# with identical target, SDK, prefix and working-directory options.
python3 tools/webkit_ci.py plan
# Readiness only; the retired parallel build entry point refuses execution.
```

WebKit uses the existing `libraries.toml` / `sources.lock.json` / `recipes/`
pipeline and `.github/workflows/libraries.yml`. It does not have a second build
or publication pipeline. Current development verification runs on `staging`
with the explicitly reviewed unpublished SDK candidate. Candidate builds cannot
publish releases. Production SDK pins, approval gates and published packages
remain separate from this verification.

The direct `webkit.py` commands are source/configuration diagnostics, not proof
of a completed port. The native backend and helper declarations still gate a
real engine build. `webkit_ci.py plan` reports readiness; `webkit_ci.py build`
rejects the retired parallel path.

The ordinary builder isolates source, SDK and verified dependencies from the OS
checkout, Git directory and credentials. Target code is not executed on Linux.
A WebKit runtime package must retain upstream DSOs, helpers, resources and
notices, and must pass native process/rendering tests before it can be described
as a working engine. No release is published by this development work.

## Local contract checks

```sh
cmake -S engines/webkit -B out/webkit-abi -DBUILD_TESTING=ON
cmake --build out/webkit-abi
ctest --test-dir out/webkit-abi --output-on-failure
python3 -m unittest discover -s tests -p 'test_webkit*.py' -v
```

A contract build is not a WebKit build. Public `mwk_get_api` works only when a real
backend supplies its hidden `mwk_backend_query` factory. Without that factory a
production link cannot succeed. The C ABI intentionally remains provisional
until the native rendering/input/process implementation is tested.

## Host-owned signed helper connection

`MWK_FEATURE_HOST_PROCESSES` (bit 8) extends context options after their original
32-byte prefix with process userdata and launch/terminate callbacks. The API
table and ABI major remain unchanged. Terra rejects engines without this feature.
The worker maps Web and Network requests to two publisher-pinned AppCore
executable dependencies. WebKit receives no filesystem executable paths. The
borrowed IPC socket is duplicated and delegated as child FD 3; argv carries the
logical WebKit identifier followed by that FD. The host retains owned Child
handles, reaps exits and prevents stale PIDs from targeting reused processes.
Launch may wait for exec on the engine worker; it never blocks ViewKit paint.
Shutdown refuses new launches before stopping and reaping existing children.

The native WPE ProcessLauncher requires the existing WPEProcessManager hook.
Missing managers and rejected launches do not fall back to pathname spawn or
`kill(pid)`. The Android path retains its upstream behavior. The mochiOS path
reports launch failure without aborting the browser process.

After a successful ordinary libs Actions build, assemble its verified target
prefix using the normal file-inventory/digest checks, then stage an independent
package (this does not sign, install, push or publish):

```sh
python3 tools/package_webkit.py --prefix /path/to/verified-prefix \
  --output /path/to/new-webkit-product --version 2.54.2
mpack create --manifest /path/to/new-webkit-product/manifest.toml \
  --payload /path/to/new-webkit-product/payload --output /path/to/WebKit.mpkg
```

The existing binary package format supports exported application helpers and
measured library binaries. DSOs preserve SONAMEs and live under
`/libraries/org.mochios.webkit`, together with exported helpers in `helpers/`.
This avoids installing private GLib/ICU/etc into the global library namespace.
The initial application loader derives discovery directories from authenticated
library records, without relaxing scoped reads or captured-byte validation.
Terra's child environment uses this same private library directory. Package
signing requires a real, explicitly selected publisher identity; the generated
manifest does not fabricate provenance or signing records.

The package generator checks the complete DT_NEEDED closure and retains license
notices. It does not claim non-code asset delegation is finished: fonts,
Fontconfig configuration, ICU/locale resources and trust data must be audited
before a production package can be enabled. Engine ports remain disabled in the
production registry. Standalone socket/helper tests are not an HTML rendering
or full-engine runtime pass. The user verifies `terra:///engine-check` only after
an updated complete engine build and final Terra link have succeeded.
