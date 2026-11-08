#include <wpe/webkit.h>
#include <jsc/jsc.h>
/* A real consumer link, never a successful browser/runtime claim on Linux. */
int main(void) {
    JSCContext *context = jsc_context_new();
    JSCValue *value = jsc_context_evaluate(context, "1 + 2", -1);
    int result = !jsc_value_is_number(value) || jsc_value_to_int32(value) != 3;
    g_object_unref(value);
    g_object_unref(context);
    return result || webkit_get_major_version() < 2;
}
