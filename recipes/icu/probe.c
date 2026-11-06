#include <unicode/unorm2.h>
#include <unicode/ubidi.h>
#include <unicode/ubrk.h>
#include <unicode/ucol.h>
#include <unicode/ustring.h>
int main(void) {
    UErrorCode error = U_ZERO_ERROR;
    const UNormalizer2 *normalizer = unorm2_getNFCInstance(&error);
    if (U_FAILURE(error) || !normalizer) return 1;
    const UChar decomposed[] = {0x0065, 0x0301};
    UChar normalized[8];
    int32_t size = unorm2_normalize(normalizer, decomposed, 2, normalized, 8, &error);
    if (U_FAILURE(error) || size != 1 || normalized[0] != 0x00e9) return 1;
    const UChar japanese[] = {0x3042, 0x3044, 0x3046};
    UBreakIterator *breaks = ubrk_open(UBRK_CHARACTER, "ja", japanese, 3, &error);
    if (U_FAILURE(error) || !breaks) return 1;
    ubrk_first(breaks);
    int count = 0;
    while (ubrk_next(breaks) != UBRK_DONE) ++count;
    ubrk_close(breaks);
    UCollator *collator = ucol_open("ja", &error);
    UBiDi *bidi = ubidi_open();
    if (!bidi) { if (collator) ucol_close(collator); return 1; }
    const UChar rtl[] = {0x05d0, 0x05d1};
    ubidi_setPara(bidi, rtl, 2, UBIDI_DEFAULT_LTR, NULL, &error);
    int failed = U_FAILURE(error) || !collator || count != 3 || ubidi_getDirection(bidi) != UBIDI_RTL;
    if (collator) ucol_close(collator);
    ubidi_close(bidi);
    return failed;
}
