#include <gio/gio.h>
extern void g_io_mochios_tls_register(void);
int main(void) {
    g_io_mochios_tls_register();
    GTlsBackend *backend = g_tls_backend_get_default();
    return !g_tls_backend_supports_tls(backend) ||
           g_strcmp0(G_OBJECT_TYPE_NAME(backend), "MochiTlsBackend");
}
