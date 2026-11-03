#include <ft2build.h>
#include FT_FREETYPE_H
int main(void) {
    FT_Library library;
    if (FT_Init_FreeType(&library)) return 1;
    FT_Int major, minor, patch;
    FT_Library_Version(library, &major, &minor, &patch);
    FT_Done_FreeType(library);
    return major != 2;
}
