#include <png.h>
int main(void) {
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, 0, 0, 0);
    if (!png) return 1;
    png_destroy_read_struct(&png, 0, 0);
    return png_get_libpng_ver(0) ? 0 : 2;
}
