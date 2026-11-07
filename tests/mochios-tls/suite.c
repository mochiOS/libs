/* Development fixture only. No custom trust root is installed in the guest. */
#define main native_tls_case_main
#include "client.c"
#undef main
#ifndef NATIVE_TLS_UNTRUSTED_PORT
#define NATIVE_TLS_UNTRUSTED_PORT 32895
#endif
#ifndef NATIVE_TLS_STALLED_PORT
#define NATIVE_TLS_STALLED_PORT 45329
#endif
#define STRINGIFY_INNER(x) #x
#define STRINGIFY(x) STRINGIFY_INNER(x)
static int wait_for_network(void) {
    if (!freopen("/tmp/native-tls-results.log", "a", stdout) ||
        !freopen("/tmp/native-tls-results.log", "a", stderr)) return 1;
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    puts("NATIVE-TLS WAIT network");
    GSocketClient *client = g_socket_client_new();
    g_socket_client_set_timeout(client, 1);
    g_socket_client_set_enable_proxy(client, FALSE);
    gint64 deadline = g_get_monotonic_time() + 10000000;
    GError *last_error = NULL;
    do {
        g_clear_error(&last_error);
        GSocketConnection *connection = g_socket_client_connect_to_host(
            client, "10.0.2.2", NATIVE_TLS_UNTRUSTED_PORT, NULL, &last_error);
        if (connection) {
            g_io_stream_close(G_IO_STREAM(connection), NULL, NULL);
            g_object_unref(connection);
            g_object_unref(client);
            puts("NATIVE-TLS READY network");
            return 0;
        }
        g_usleep(100000);
    } while (g_get_monotonic_time() < deadline);
    g_object_unref(client);
    return fail("network-readiness", last_error);
}
int main(void) {
    if (wait_for_network()) { puts("NATIVE-TLS SUITE failures=1"); return 1; }
    char *https[] = {"native-tls", "https", "https://example.com/", NULL};
    char *reject[] = {"native-tls", "reject-untrusted", "10.0.2.2",
                      STRINGIFY(NATIVE_TLS_UNTRUSTED_PORT), NULL};
    char *cancel[] = {"native-tls", "cancel", "10.0.2.2",
                      STRINGIFY(NATIVE_TLS_STALLED_PORT), NULL};
    int failures = native_tls_case_main(3, https);
    failures += native_tls_case_main(4, reject);
    failures += native_tls_case_main(4, cancel);
    printf("NATIVE-TLS SUITE failures=%d\n", failures);
    return failures ? 1 : 0;
}
