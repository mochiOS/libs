#include <iconv.h>
#include <string.h>
int main(void) {
    iconv_t converter = iconv_open("UTF-16LE", "UTF-8");
    if (converter == (iconv_t)-1) return 1;
    char input[] = "mochi", output[16] = {0};
    char *in = input, *out = output;
    size_t remaining = 5, available = sizeof output;
    size_t result = iconv(converter, &in, &remaining, &out, &available);
    iconv_close(converter);
    return result == (size_t)-1 || remaining != 0 || out - output != 10;
}
