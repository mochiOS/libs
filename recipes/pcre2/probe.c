#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
int main(void) {
    int error;
    PCRE2_SIZE offset;
    pcre2_code *code = pcre2_compile((PCRE2_SPTR)"[a-z]+", PCRE2_ZERO_TERMINATED,
                                    PCRE2_UTF, &error, &offset, NULL);
    if (!code) return 1;
    pcre2_match_data *data = pcre2_match_data_create_from_pattern(code, NULL);
    int result = pcre2_match(code, (PCRE2_SPTR)"mochi", 5, 0, 0, data, NULL);
    pcre2_match_data_free(data);
    pcre2_code_free(code);
    return result < 0;
}
