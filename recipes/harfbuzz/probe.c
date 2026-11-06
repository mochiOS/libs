#include <hb.h>
#include <hb-icu.h>
#include <hb-glib.h>
#include <hb-ft.h>
int main(void) {
    hb_unicode_funcs_t *icu = hb_icu_get_unicode_funcs();
    hb_unicode_funcs_t *glib = hb_glib_get_unicode_funcs();
    hb_codepoint_t composed = 0;
    if (!hb_unicode_compose(icu, 'e', 0x0301, &composed) || composed != 0x00e9) return 1;
    if (hb_unicode_script(glib, 0x3042) != HB_SCRIPT_HIRAGANA) return 1;
    /* Make the FreeType adapter part of the actual import/link probe. */
    if (hb_ft_font_get_ft_face(hb_font_get_empty()) != NULL) return 1;
    hb_buffer_t *buffer = hb_buffer_create();
    hb_buffer_set_unicode_funcs(buffer, icu);
    hb_buffer_add_utf8(buffer, "hello", -1, 0, -1);
    hb_buffer_guess_segment_properties(buffer);
    hb_shape(hb_font_get_empty(), buffer, NULL, 0);
    unsigned int count = hb_buffer_get_length(buffer);
    hb_buffer_destroy(buffer);
    return count != 5;
}
