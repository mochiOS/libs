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
- Actions checks the contract and saves an explicit readiness plan. The real
  cross-build job runs only when dependency recipes, backend source and helper
  Capability declarations are available. A skipped build is not a built engine.

The native backend is **not implemented yet**. Current missing dependency ports
include EGL, libepoxy and xkbcommon. No working WebKit engine, native WebView or
release package is claimed by these changes. `port.json` is a reviewable record
of missing backend and helper definitions, not a Boolean switch that fabricates
support. Actual SDK/prefix versions and upstream CMake still decide compilation.

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
contracts. This adapter cannot be responsibly connected until the WPE platform
backend actually renders and processes those events; no placeholder component
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
python3 tools/webkit_ci.py build
```

`webkit_ci.py build` refuses missing prerequisites before downloading/building.
Once ready it reuses verified SDK-locked dependency releases, captures exact input
hashes, and runs WebKit in the pinned builder image with network disabled, dropped
capabilities and a read-only root. Only isolated source/SDK/dependency mounts and
private output are supplied. Only reviewed pipeline files are mounted: no OS
checkout, Git directory, signing key or GitHub token enters the build container.
The CI orchestrator applies only the reviewed `port.json` patch list to its
private native source tree before configuration. Changed patches require a fresh
build directory; the pinned upstream archive stays unchanged. Direct driver
commands operate on the supplied source tree and do not imply a completed port.
All configure/build/install output is streamed into Actions and retained in logs.
Default downloads, extracted sources and build caches live under ignored
`out/webkit`, separate from the tracked adapter `src/` directory.
The initial 25 GiB budget is a preflight policy, not an upstream minimum. Set the
repository variable `WEBKIT_BUILD_RUNNER` to a JSON runner-label array if a larger
runner is needed; no runner configuration or remote workflow was changed here.

The developer archive preserves the complete installed tree and license notices.
Its receipt says `runtime_tested=false` and `publishable=false`. It is neither a
signed MPKG nor a working-browser claim. Native process/surface/sandbox tests and
explicit helper Capability declarations are required before a production package.
The workflow uploads artifacts only and contains no release/signing job.

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
