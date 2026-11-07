/* Self-contained keymap/compose exercise: no invented OS keyboard layout/data. */
#include <xkbcommon/xkbcommon.h>
#include <xkbcommon/xkbcommon-compose.h>
#include <stdio.h>
#include <string.h>
static const char keymap[] =
"xkb_keymap {\n"
"xkb_keycodes \"test\" { minimum=8; maximum=255; <AC01>=38; };\n"
"xkb_types \"test\" { type \"TWO_LEVEL\" { modifiers=Shift; map[Shift]=Level2; level_name[Level1]=\"Base\"; level_name[Level2]=\"Shift\"; }; };\n"
"xkb_compatibility \"test\" {};\n"
"xkb_symbols \"test\" { key <AC01> { type=\"TWO_LEVEL\", [ a, A ] }; };\n"
"};\n";
int main(void) {
    int result=1;
    struct xkb_context *context=xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES|XKB_CONTEXT_NO_ENVIRONMENT_NAMES);
    struct xkb_keymap *map=NULL;
    struct xkb_state *state=NULL;
    struct xkb_compose_table *table=NULL;
    struct xkb_compose_state *compose=NULL;
    if(!context)goto done;
    if(xkb_keysym_to_utf32(XKB_KEY_a)!='a'||xkb_keysym_from_name("Return",XKB_KEYSYM_NO_FLAGS)!=XKB_KEY_Return)goto done;
    map=xkb_keymap_new_from_string(context,keymap,XKB_KEYMAP_FORMAT_TEXT_V1,XKB_KEYMAP_COMPILE_NO_FLAGS);
    if(!map)goto done;
    state=xkb_state_new(map);if(!state||xkb_state_key_get_utf32(state,38)!='a')goto done;
    xkb_mod_index_t shift=xkb_keymap_mod_get_index(map,XKB_MOD_NAME_SHIFT);
    if(shift==XKB_MOD_INVALID||shift>=32)goto done;
    xkb_state_update_mask(state,(xkb_mod_mask_t)1<<shift,0,0,0,0,0);
    if(xkb_state_key_get_utf32(state,38)!='A')goto done;
    const char text[]="<Multi_key> <a> <e> : \"æ\" U00E6\n";
    table=xkb_compose_table_new_from_buffer(context,text,sizeof(text)-1,"C",XKB_COMPOSE_FORMAT_TEXT_V1,XKB_COMPOSE_COMPILE_NO_FLAGS);
    if(!table)goto done;
    compose=xkb_compose_state_new(table,XKB_COMPOSE_STATE_NO_FLAGS);if(!compose)goto done;
    xkb_compose_state_feed(compose,XKB_KEY_Multi_key);
    xkb_compose_state_feed(compose,XKB_KEY_a);
    xkb_compose_state_feed(compose,XKB_KEY_e);
    char output[16];
    if(xkb_compose_state_get_status(compose)!=XKB_COMPOSE_COMPOSED||xkb_compose_state_get_utf8(compose,output,sizeof(output))!=2||strcmp(output,"æ"))goto done;
    result=0;
done:
    xkb_compose_state_unref(compose);xkb_compose_table_unref(table);
    xkb_state_unref(state);xkb_keymap_unref(map);xkb_context_unref(context);
    puts(result?"XKBCOMMON FAIL":"XKBCOMMON PASS");
    return result;
}
