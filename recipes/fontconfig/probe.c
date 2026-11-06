#include <fontconfig/fontconfig.h>
#include <string.h>
int main(void) {
    FcConfig *config = FcConfigCreate();
    FcPattern *pattern = FcNameParse((const FcChar8 *)"sans-serif:lang=ja");
    FcCharSet *charset = FcCharSetCreate();
    if (!config || !pattern || !charset) return 1;
    int failed = !FcCharSetAddChar(charset, 0x3042) || !FcCharSetHasChar(charset, 0x3042);
    failed |= !FcPatternAddCharSet(pattern, FC_CHARSET, charset);
    FcChar8 *family = 0;
    failed |= FcPatternGetString(pattern, FC_FAMILY, 0, &family) != FcResultMatch;
    failed |= !family || strcmp((const char *)family, "sans-serif");
    failed |= !FcConfigParseAndLoadFromMemory(config, (const FcChar8 *)"<fontconfig><dir>/system/libraries/fonts</dir></fontconfig>", FcTrue);
    FcCharSetDestroy(charset);
    FcPatternDestroy(pattern);
    FcConfigDestroy(config);
    return failed;
}
