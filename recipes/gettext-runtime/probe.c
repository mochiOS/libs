#include <libintl.h>
#include <string.h>
int main(void) {
    bindtextdomain("mochi", "/system/share/locale");
    bind_textdomain_codeset("mochi", "UTF-8");
    textdomain("mochi");
    const char *message = gettext("Library probe");
    const char *plural = ngettext("item", "items", 2);
    return !message || !plural || strcmp(plural, "items") != 0;
}
