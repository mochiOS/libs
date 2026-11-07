# Native GIO TLS provider

`glib-networking` builds `libgiomochios.so` from fixed upstream 2.90.1 sources
plus the repository-owned `recipes/glib-networking/mochios.patch`. It retains
upstream GIO stream, async and cancellation machinery and connects it to the
existing mochiOS TLS verifier through the SDK's private PIC record archive.
No new OS service, AppCore API, socket implementation, GnuTLS dependency or
certificate store is introduced.

## Linking and ownership

Published SDK 27.0-dp.1-shared.4 supplies `lib/libmochios_tls_pic.a` and
`sysroot/include/mochios/tls.h`. This archive is for a DSO whose version script
hides all Rust symbols, not for direct linking into a C executable. The process
continues to own allocation, threading, entropy and time. `libgiomochios.so`
exports only `g_io_mochios_tls_register` and registers itself at startup.
`gio-mochios-tls.pc` brackets its library with `--no-as-needed`/`--as-needed` so
applications calling only generic GIO APIs retain the provider. Runtime
`dlopen` is not required. On mochiOS, libsoup requires this provider explicitly;
the target's actual `DT_NEEDED` entry and final consumer link were verified.

The C ABI exposes opaque integer sessions, bounded queues, partial ciphertext
draining, verified metadata and ALPN. Session IDs are not reused. Fork children
invalidate inherited sessions so copied traffic keys cannot reuse nonces.
Plaintext queues and C scratch storage are wiped after consumption.
The header documents buffer ownership and errors.

## Security and current limits

Production trust and verification remain in `mochios-tls-client`. The GIO
backend rejects custom trust stores and relaxed verification flags; an
`accept-certificate` callback cannot approve an invalid peer. Host-only test
roots cannot be built for mochiOS. The first provider supports TLS 1.3 clients
with canonical ASCII DNS identities, following existing native policy. IP
identities, Unicode conversion, custom roots, client certificates, server TLS
and resumption are explicitly unsupported. Admission is bounded to 16 sessions.

## Tests

On Linux, with the pinned user Rust toolchain, a C compiler, Meson, Ninja and
GIO development headers installed:

```sh
python3 tests/gio-tls/run.py \
  --user-source /path/to/syscalls \
  --glib-networking-source /path/to/glib-networking-git-cache \
  --output /path/to/fresh-test-output
```

The Git cache must contain the commit in `sources.lock.json`; the runner uses
that exact commit, not the checkout contents. It builds a separate host
fixture and checks C ownership/fork behavior, real TLS 1.3 HTTP traffic,
sync/async reads, ALPN, buffered readiness, authenticated shutdown, hostname
failure, verification-policy rejection and cancellation. No test CA is
installed in the host or OS. Rust tests separately check tampering,
fragmentation, certificate rejection and queue backpressure before nonce use.

## Native mochiOS verification

Both ports are enabled in the distribution catalog. The published shared.4 SDK
was pinned by its archive hash and used to compile and cross-link the libraries,
C executable consumers and DSO consumers. A real SDK C client ran through the
normal scoped application launcher and authenticated built-in package graph in
an isolated, Secure Boot verified mochiOS VM. It completed:

- HTTPS GET: TLS 1.3, HTTP/2, status 200 and a nonempty response.
- Rejection of a self-signed server with G_TLS_ERROR_BAD_CERTIFICATE.
- Asynchronous handshake cancellation with G_IO_ERROR_CANCELLED.

See `native-tls-verification.json` and `native-tls-verification.log` for the
exact SDK, library source commits, image/kernel hashes and guest results.
`tests/mochios-tls/reproduce.md` documents reproduction. These cases do not claim
that the complete upstream suites or every SDK API passed. In particular, the
SDK C execv path rejected an already-prepared dynamic image; this fixture uses
the normal AppCore/Binder scoped launch path and does not bypass authorization.

Validation exposed a driver bug: its empty receive response negated an already
negative compatibility EAGAIN, returning +11. The strict receiver rejected that
malformed status and reset the interface. The driver now preserves -11/-5;
regression tests exercise the actual driver receive/transmit dispatcher.

The full pinned production-container graph has not been run locally (Docker is
unavailable). The normal build, verification and maintainer approval workflow
is still required before publication. No release or push was performed here.
