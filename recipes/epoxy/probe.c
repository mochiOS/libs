/* Exercise dispatch metadata and honest absence of an EGL provider. */
#include <epoxy/egl.h>
#include <stdio.h>
int main(void) {
    if (!epoxy_extension_in_string("GL_EXT_one GL_EXT_two", "GL_EXT_one") ||
        !epoxy_extension_in_string("GL_EXT_one GL_EXT_two", "GL_EXT_two") ||
        epoxy_extension_in_string("GL_EXT_one GL_EXT_two", "GL_EXT_on") ||
        epoxy_extension_in_string("GL_EXT_one GL_EXT_two", "EXT_two") ||
        epoxy_extension_in_string("", "GL_EXT_one")) {
        puts("EPOXY FAIL extension tokens"); return 1;
    }
    /* This test environment intentionally installs no EGL implementation. */
    if (epoxy_has_egl()) { puts("EPOXY FAIL unexpected EGL provider"); return 2; }
    puts("EPOXY PASS metadata and missing-provider handling (no rendering tested)");
    return 0;
}
