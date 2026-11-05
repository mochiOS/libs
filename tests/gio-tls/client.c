#include <gio/gio.h>
#include <string.h>
#include <stdlib.h>
static gboolean notified, handshake_done, handshake_ok;
static GError *handshake_error;
static gboolean read_done;
static gssize async_count;
static GError *read_error;
static void received(GObject *source,GAsyncResult *result,gpointer data) {
    async_count=g_input_stream_read_finish(G_INPUT_STREAM(source),result,&read_error);
    read_done=TRUE;
}
static void handshaken(GObject *source,GAsyncResult *result,gpointer data) {
    handshake_ok=g_tls_connection_handshake_finish(G_TLS_CONNECTION(source),result,&handshake_error);
    handshake_done=TRUE;
}
static gboolean accept_any(GTlsConnection *connection,GTlsCertificate *peer,GTlsCertificateFlags flags,gpointer data) {return TRUE;}
static gboolean cancel_later(gpointer data) {g_cancellable_cancel(data);return G_SOURCE_REMOVE;}

static gboolean ready(GObject *stream,gpointer data) {notified=TRUE;return G_SOURCE_REMOVE;}
int main(int argc,char **argv)
{
    GError *error=NULL;GSocketClient *client=g_socket_client_new();GSocketConnection *socket;
    GSocketConnectable *identity;GIOStream *tls;const char *protocols[]={"http/1.1",NULL};
    gchar buffer[4096];gsize written;gssize count;gint64 deadline;GSource *source;
    const char *mode=argc>2?argv[2]:"sync";
    g_assert(argc>=2);g_assert_cmpstr(G_OBJECT_TYPE_NAME(g_tls_backend_get_default()),==,"MochiTlsBackend");
    g_assert(g_tls_backend_supports_tls(g_tls_backend_get_default()));
    socket=g_socket_client_connect_to_host(client,"127.0.0.1",atoi(argv[1]),NULL,&error);
    g_assert_no_error(error);g_assert_nonnull(socket);
    identity=g_network_address_new(!strcmp(mode,"wrong-host")?"wrong.test.mochios":"tls.test.mochios",atoi(argv[1]));
    tls=g_tls_client_connection_new(G_IO_STREAM(socket),identity,&error);g_assert_no_error(error);g_assert_nonnull(tls);
    g_tls_connection_set_advertised_protocols(G_TLS_CONNECTION(tls),protocols);
    if (!strcmp(mode,"wrong-host")) {
        g_signal_connect(tls,"accept-certificate",G_CALLBACK(accept_any),NULL);
        g_assert(!g_tls_connection_handshake(G_TLS_CONNECTION(tls),NULL,&error));
        g_assert_error(error,G_TLS_ERROR,G_TLS_ERROR_BAD_CERTIFICATE);goto cleanup;
    }
    if (!strcmp(mode,"relaxed")) {
        g_object_set(tls,"validation-flags",0,NULL);
        g_assert(!g_tls_connection_handshake(G_TLS_CONNECTION(tls),NULL,&error));
        g_assert_error(error,G_IO_ERROR,G_IO_ERROR_NOT_SUPPORTED);goto cleanup;
    }
    if (!strcmp(mode,"cancel")) {
        GCancellable *cancel=g_cancellable_new();
        g_tls_connection_handshake_async(G_TLS_CONNECTION(tls),0,cancel,handshaken,NULL);
        g_timeout_add(50,cancel_later,cancel);
        while(!handshake_done) g_main_context_iteration(NULL,TRUE);
        g_assert(!handshake_ok);g_assert_error(handshake_error,G_IO_ERROR,G_IO_ERROR_CANCELLED);
        g_clear_error(&handshake_error);g_object_unref(cancel);goto cleanup;
    }
    if (!strcmp(mode,"async")) {
        g_tls_connection_handshake_async(G_TLS_CONNECTION(tls),0,NULL,handshaken,NULL);
        while(!handshake_done) g_main_context_iteration(NULL,TRUE);
        g_assert(handshake_ok);g_assert_no_error(handshake_error);
    } else {
        g_assert(g_tls_connection_handshake(G_TLS_CONNECTION(tls),NULL,&error));g_assert_no_error(error);
    }

    g_assert_cmpstr(g_tls_connection_get_negotiated_protocol(G_TLS_CONNECTION(tls)),==,"http/1.1");
    g_assert(g_tls_connection_get_protocol_version(G_TLS_CONNECTION(tls))==G_TLS_PROTOCOL_VERSION_TLS_1_3);
    g_assert_nonnull(g_tls_connection_get_peer_certificate(G_TLS_CONNECTION(tls)));
    g_assert(g_output_stream_write_all(g_io_stream_get_output_stream(tls),"GET / HTTP/1.1\r\nHost: tls.test.mochios\r\n\r\n",strlen("GET / HTTP/1.1\r\nHost: tls.test.mochios\r\n\r\n"),&written,NULL,&error));g_assert_no_error(error);
    g_assert(g_output_stream_flush(g_io_stream_get_output_stream(tls),NULL,&error));g_assert_no_error(error);
    if (!strcmp(mode,"async")) {
        g_input_stream_read_async(g_io_stream_get_input_stream(tls),buffer,3,0,NULL,received,NULL);
        while(!read_done) g_main_context_iteration(NULL,TRUE);
        g_assert_no_error(read_error);count=async_count;
    } else {
        count=g_input_stream_read(g_io_stream_get_input_stream(tls),buffer,3,NULL,&error);
        g_assert_no_error(error);
    }
    g_assert_cmpint(count,==,3);g_assert(!memcmp(buffer,"HTT",3));
    g_assert(g_pollable_input_stream_is_readable(G_POLLABLE_INPUT_STREAM(g_io_stream_get_input_stream(tls))));
    source=g_pollable_input_stream_create_source(G_POLLABLE_INPUT_STREAM(g_io_stream_get_input_stream(tls)),NULL);
    g_source_set_callback(source,(GSourceFunc)ready,NULL,NULL);g_source_attach(source,NULL);
    deadline=g_get_monotonic_time()+1000000;
    while(!notified && g_get_monotonic_time()<deadline) {g_main_context_iteration(NULL,FALSE);g_usleep(1000);}
    g_assert(notified);g_source_destroy(source);g_source_unref(source);
    GByteArray *response=g_byte_array_new();
    g_byte_array_append(response,(guint8 *)buffer,3);
    while((count=g_input_stream_read(g_io_stream_get_input_stream(tls),buffer,sizeof(buffer),NULL,&error))>0)
        g_byte_array_append(response,(guint8 *)buffer,count);
    g_assert_no_error(error);g_assert_cmpint(count,==,0);
    const char *header="HTTP/1.1 200 OK\r\nContent-Length: 1024\r\n\r\n";
    g_assert_cmpuint(response->len,==,strlen(header)+1024);
    g_assert(!memcmp(response->data,header,strlen(header)));
    for(gsize i=strlen(header);i<response->len;i++) g_assert(response->data[i]=='x');
    g_byte_array_unref(response);
    g_assert(g_io_stream_close(tls,NULL,&error));g_assert_no_error(error);
cleanup:
    g_clear_error(&error);
    g_object_unref(tls);g_object_unref(identity);g_object_unref(socket);g_object_unref(client);
    return 0;
}
