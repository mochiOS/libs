# Native C library probes

This suite runs a recipe's real C `int main(void)` probe as a normal built-in
application in a disposable development image. It does not replace a library
with a host/fake implementation or grant service privileges. The application
launcher uses ordinary SDK `execv`; all target DSOs have digest-bound package
binary declarations and are loaded through the normal authenticated graph.

```sh
python3 tests/mochios-ports/build.py --library xkbcommon \
  --os-root "$OS_ROOT" --sdk "$SDK_CANDIDATE" --prefix "$TARGET_PREFIX" \
  --capability-binary "$MATCHING_CAPABILITY_BINARY" --output out/native-xkb-build
python3 tests/mochios-tls/image.py --os-root "$OS_ROOT" \
  --stage out/native-xkb-build/stage --output out/native-xkb-vm
python3 tests/mochios-ports/run.py --image-directory out/native-xkb-vm \
  --build out/native-xkb-build/build.json
```

The SDK candidate must contain the authenticated dynamic-exec runtime and its
matching capability service. Published shared.4 remains unchanged. As with the
TLS/subprocess suites, the image requires an existing development configuration
and automatic login; the composer never changes authentication/Capability policy.
A frozen standalone source image may be selected with `--base-image-directory`.

Use fresh output directories. Success requires exactly one start marker and
one `PORT-SMOKE COMPLETE result=0`. Missing results, a nonzero return, guest fault,
duplicate markers or timeout fail. The guest log is `/tmp/port-smoke-results.log`.
`result.json` records actual runtime/DSO/executable and source/image hashes.

The xkbcommon probe tests keysym/Unicode conversion, an explicit in-memory
keymap, the Shift modifier and UTF-8 Compose output. It deliberately does not
invent or install an OS-wide keyboard layout. xkeyboard-config/locale databases,
X11/Wayland backends, xkbregistry and CLI tools are not included in this port.
Default rules-name/file lookups require separately installed, authorized data;
missing files remain errors. Native smoke success is not the whole upstream
suite or a completed WebKit input backend.

The published SDK declares but does not export `secure_getenv`. The private
mochiOS port helper therefore uses real `AT_SECURE` and current credential APIs,
fails closed for missing security data or credential mismatches/failures, and
preserves errno. It does not fall back to an unconditional getenv. Host C tests
exercise these rejection cases separately from the native keyboard smoke test.

## libepoxy (development SDK only)

The 1.5.10 dispatch library is ported without GLX, X11, tests or documentation
executables. It supplies **no EGL implementation**. Its private Khronos headers
retain their complete notices and are pinned to EGL-Registry commit
`8e56e86e65687697ef54114cdb1afabdfac756bf`. They are installed under
`epoxy/khronos`, not the public `EGL`/`KHR` namespace. mochiOS uses the existing
Khronos platform-neutral pointer types; these are not ViewKit native handles.

Published shared.4 has no target `dlfcn.h` or exported dynamic-load ABI. The
recipe therefore remains disabled in the release catalog. Do not remove the
gate merely because local development SDK compilation succeeds.

To reproduce the loader-enabled development candidate from the measured
SDK candidate used above:

```sh
python3 tests/mochios-ports/prepare-loader-sdk.py \
  --base "$SDK_CANDIDATE" \
  --dlfcn-header "$OS_ROOT/user/libc-port/include/dlfcn.h" \
  --output out/epoxy-sdk
```

The tool verifies all four functions exist in the actual runtime archive,
roots them in an actual linked executable, and measures its exported symbols.
It detaches each modified hardlink before writing. The base and published SDK
remain unchanged; no release is generated. Use this candidate to cross-build
the recipe, then run the native fixture commands with `--library epoxy` and the
resulting target-only prefix. No system EGL package belongs in that prefix.

Epoxy revision 4 links the real Mesa EGL provider at startup. Its native probe
creates a surfaceless pbuffer, resolves GLES functions through that provider,
clears the surface and checks returned RGBA pixels. The bootstrap uses the
advertised `EGL_EXT_platform_base` entry point, not an EGL 1.5 function before
initialization. The measured provider is Mesa softpipe; this does not establish
hardware acceleration. See `tooling/epoxy-verification.json` for the verified
Actions bundles and matching native binary hashes.

Late loading of Mesa's initial-exec TLS remains unsupported. No runtime loader
permissions or TLS constraints are relaxed by the startup-linked integration.

## WebKit thread and memory prerequisites

The native `pthread-suspend.c` probe interrupts a busy guarded worker thread,
reads its real SDK `ucontext_t`, checks the interrupted SP against observed stack
bounds, and performs WTF's semaphore / signal / `sigsuspend` resume handshake.
It checks that the C handler uses the interrupted user stack below the x86-64
red zone and that the resume handler actually ran. A notification or partial stage log is insufficient.

```sh
python3 tests/mochios-ports/build.py \
  --probe tests/mochios-ports/pthread-suspend.c \
  --probe-entry native_gc_suspend_probe \
  --os-root "$OS_ROOT" --sdk "$SDK_CANDIDATE" --prefix "$TARGET_PREFIX" \
  --capability-binary "$MATCHING_CAPABILITY_BINARY" --output out/native-gc-build
python3 tests/mochios-tls/image.py --os-root "$OS_ROOT" \
  --stage out/native-gc-build/stage --output out/native-gc-vm \
  --kernel-directory "$MATCHING_KERNEL" \
  --base-image-directory "$FROZEN_BASE_IMAGE"
python3 tests/mochios-ports/run.py --image-directory out/native-gc-vm \
  --build out/native-gc-build/build.json
```

Use the exact reviewed SDK Actions artifact, not the earlier shared.6 candidate
that hung inside the resume wait. The runtime uses an empty acceptance set in
the existing retained POSIX signal-wait request: this waits for delivery without
consuming a signal or depending on nested kernel upcalls. The handler generation
must change before `sigsuspend` returns `EINTR`; ignored/epoch-only wakeups retry.

The SDK's `sdk/probe/process-memory.c` independently checks current resident
bytes, the lifetime peak, allocation/deallocation observations, and size/null
errors through `mochios_process_memory_read`. It can be run with the same fixture
builder using `--probe-entry sdk_memory_probe` and an explicit probe path.

These are WebKit prerequisites. They do not execute JavaScriptCore, launch WPE's
helpers, render HTML or verify the proposed `libwebkit.so.1` backend.
