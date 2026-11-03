#include <stdio.h>
#include <jpeglib.h>
#include <turbojpeg.h>
int main(void) {
    struct jpeg_decompress_struct decoder;
    struct jpeg_error_mgr error;
    decoder.err = jpeg_std_error(&error);
    jpeg_create_decompress(&decoder);
    jpeg_destroy_decompress(&decoder);
    tjhandle turbo = tjInitDecompress();
    if (!turbo) return 1;
    return tjDestroy(turbo);
}
