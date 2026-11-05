#include <idn2.h>
#include <stdint.h>
#include <string.h>
int main(void) {
    uint8_t *ascii = 0;
    const uint8_t domain[] = "b\xc3\xbc" "cher.example";
    int result = idn2_lookup_u8(domain, &ascii, IDN2_NONTRANSITIONAL);
    int failed = result != IDN2_OK || !ascii || strcmp((const char *)ascii, "xn--bcher-kva.example");
    idn2_free(ascii);
    return failed;
}
