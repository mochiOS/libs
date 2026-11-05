#include <libsoup/soup.h>
#include <string.h>
int main(void) {
    if (!g_tls_backend_supports_tls(g_tls_backend_get_default()) ||
        g_strcmp0(G_OBJECT_TYPE_NAME(g_tls_backend_get_default()), "MochiTlsBackend")) return 1;
    SoupSession *session = soup_session_new();
    SoupMessage *message = soup_message_new("GET", "https://example.com/");
    SoupCookieJar *jar = soup_cookie_jar_new();
    if (!session || !message || !jar) return 1;
    soup_session_add_feature(session, SOUP_SESSION_FEATURE(jar));
    SoupMessageHeaders *headers = soup_message_get_request_headers(message);
    soup_message_headers_append(headers, "Accept", "text/html");
    const char *accept = soup_message_headers_get_one(headers, "Accept");
    int failed = !accept || strcmp(accept, "text/html");
    SoupCookieJar *database = soup_cookie_jar_db_new(":memory:", FALSE);
    if (!database) failed = 1;
    if (database) g_object_unref(database);
    g_object_unref(jar);
    g_object_unref(message);
    g_object_unref(session);
    return failed;
}
