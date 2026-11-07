/* Run on mochiOS only; this client never changes certificate trust or policy. */
#include <gio/gio.h>
#include <libsoup/soup.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int fail(const char *step, GError *error) {
    fprintf(stderr, "NATIVE-TLS FAIL step=%s domain=%s code=%d message=%s\n", step,
            error ? g_quark_to_string(error->domain) : "none", error ? error->code : 0,
            error ? error->message : "unexpected result");
    g_clear_error(&error);
    return 1;
}
static gboolean finished;
static gboolean handshake_ok;
static GError *handshake_error;
static void handshaken(GObject *object, GAsyncResult *result, gpointer unused) {
    (void)unused;
    handshake_ok = g_tls_connection_handshake_finish(G_TLS_CONNECTION(object), result, &handshake_error);
    finished = TRUE;
}
static gboolean cancel_handshake(gpointer cancellable) {
    g_cancellable_cancel(cancellable);
    return G_SOURCE_REMOVE;
}
static int transport_case(const char *mode, const char *host, int port) {
    GError *error = NULL;
    GSocketClient *client = g_socket_client_new();
    g_socket_client_set_timeout(client, 10);
    GSocketConnection *socket = g_socket_client_connect_to_host(client, host, port, NULL, &error);
    if (!socket) return fail("connect", error);
    GSocketConnectable *identity = g_network_address_new("tls.test.mochios", port);
    GIOStream *tls = g_tls_client_connection_new(G_IO_STREAM(socket), identity, &error);
    if (!tls) return fail("create TLS", error);
    if (!strcmp(mode, "reject-untrusted")) {
        if (g_tls_connection_handshake(G_TLS_CONNECTION(tls), NULL, &error))
            return fail("untrusted certificate accepted", NULL);
        if (!g_error_matches(error, G_TLS_ERROR, G_TLS_ERROR_BAD_CERTIFICATE))
            return fail("certificate error classification", error);
        g_clear_error(&error);
    } else {
        GCancellable *cancellable = g_cancellable_new();
        g_tls_connection_handshake_async(G_TLS_CONNECTION(tls), 0, cancellable, handshaken, NULL);
        g_timeout_add(100, cancel_handshake, cancellable);
        gint64 deadline = g_get_monotonic_time() + 10000000;
        while (!finished && g_get_monotonic_time() < deadline) {
            g_main_context_iteration(NULL, FALSE);
            g_usleep(1000);
        }
        if (!finished || handshake_ok || !g_error_matches(handshake_error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
            return fail("asynchronous cancellation", handshake_error);
        g_clear_error(&handshake_error);
        g_object_unref(cancellable);
    }
    g_io_stream_close(tls, NULL, NULL);
    g_object_unref(tls);
    g_object_unref(identity);
    g_object_unref(socket);
    g_object_unref(client);
    printf("NATIVE-TLS PASS mode=%s\n", mode);
    return 0;
}
int main(int argc, char **argv) {
    if (!freopen("/tmp/native-tls-results.log", "a", stdout) ||
        !freopen("/tmp/native-tls-results.log", "a", stderr)) return 2;
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    printf("NATIVE-TLS START time=%lld\n", (long long)time(NULL));
    if (time(NULL) < 1700000000) return fail("clock", NULL);
    unsigned char random[32];
    arc4random_buf(random, sizeof random);
    unsigned char any = 0;
    for (size_t i = 0; i < sizeof random; ++i) any |= random[i];
    if (!any) return fail("entropy", NULL);
    GTlsBackend *backend = g_tls_backend_get_default();
    printf("NATIVE-TLS backend=%s\n", G_OBJECT_TYPE_NAME(backend));
    if (strcmp(G_OBJECT_TYPE_NAME(backend), "MochiTlsBackend") || !g_tls_backend_supports_tls(backend))
        return fail("backend", NULL);
    if (argc == 4 && (!strcmp(argv[1], "reject-untrusted") || !strcmp(argv[1], "cancel")))
        return transport_case(argv[1], argv[2], atoi(argv[3]));
    if (argc != 3 || strcmp(argv[1], "https")) return fail("arguments", NULL);
    GError *error = NULL;
    SoupSession *session = soup_session_new_with_options("timeout", 20, "max-conns", 2, NULL);
    SoupMessage *message = soup_message_new("GET", argv[2]);
    if (!message) return fail("URL", NULL);
    GBytes *bytes = soup_session_send_and_read(session, message, NULL, &error);
    if (!bytes) return fail("HTTPS", error);
    guint status = soup_message_get_status(message);
    gsize size = g_bytes_get_size(bytes);
    GTlsProtocolVersion tls = soup_message_get_tls_protocol_version(message);
    printf("NATIVE-TLS HTTPS status=%u bytes=%zu tls=%d http=%d\n", status, size, tls,
           soup_message_get_http_version(message));
    if (status != 200 || !size || tls != G_TLS_PROTOCOL_VERSION_TLS_1_3)
        return fail("response", NULL);
    g_bytes_unref(bytes);
    g_object_unref(message);
    soup_session_abort(session);
    g_object_unref(session);
    puts("NATIVE-TLS PASS mode=https");
    return 0;
}
