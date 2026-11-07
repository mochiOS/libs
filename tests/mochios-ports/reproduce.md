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
