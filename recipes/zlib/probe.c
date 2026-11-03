#include <zlib.h>
int main(void) {
    unsigned char compressed[64], restored[64];
    const unsigned char data[] = "mochiOS library link probe";
    uLongf size = sizeof(compressed), restored_size = sizeof(restored);
    if (!zlibVersion()) return 1;
    if (compress2(compressed, &size, data, sizeof(data), Z_DEFAULT_COMPRESSION) != Z_OK) return 2;
    return uncompress(restored, &restored_size, compressed, size) == Z_OK ? 0 : 3;
}
