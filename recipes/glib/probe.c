#include <gio/gio.h>
#include <gmodule.h>
int main(void) {
    GString *text = g_string_new("mochi");
    g_string_append(text, "OS");
    GObject *object = g_object_new(G_TYPE_OBJECT, NULL);
    GInputStream *stream = g_memory_input_stream_new_from_data(text->str, text->len, NULL);
    char buffer[8] = {0};
    GError *error = NULL;
    gssize count = g_input_stream_read(stream, buffer, sizeof buffer, NULL, &error);
    GMainContext *context = g_main_context_new();
    g_main_context_pending(context);
    g_main_context_unref(context);
    (void)g_module_supported();
    g_object_unref(stream);
    g_object_unref(object);
    g_string_free(text, TRUE);
    if (error) { g_error_free(error); return 1; }
    return count != 7;
}
