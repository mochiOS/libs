#include <uninorm.h>
#include <unicase.h>
#include <unistr.h>
#include <stdlib.h>
int main(void) {
    const uint8_t input[] = {0x65, 0xcc, 0x81};
    const uint8_t expected[] = {0xc3, 0xa9};
    size_t size = 0;
    uint8_t *normalized = u8_normalize(UNINORM_NFC, input, sizeof input, 0, &size);
    int failed = !normalized || size != sizeof expected || u8_cmp(normalized, expected, size);
    free(normalized);
    return failed || uc_tolower(0x0041) != 0x0061;
}
