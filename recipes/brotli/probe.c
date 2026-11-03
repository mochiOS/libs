#include <brotli/encode.h>
#include <brotli/decode.h>
#include <string.h>
int main(void) {
    const unsigned char input[] = "mochiOS";
    unsigned char encoded[128], output[16];
    size_t encoded_size = sizeof encoded, output_size = sizeof output;
    if (!BrotliEncoderCompress(5, BROTLI_DEFAULT_WINDOW, BROTLI_MODE_GENERIC,
                              sizeof input, input, &encoded_size, encoded)) return 1;
    if (BrotliDecoderDecompress(encoded_size, encoded, &output_size, output) != BROTLI_DECODER_RESULT_SUCCESS) return 1;
    return output_size != sizeof input || memcmp(input, output, sizeof input) != 0;
}
