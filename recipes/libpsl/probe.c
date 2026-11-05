#include <libpsl.h>
#include <string.h>
int main(void) {
    const psl_ctx_t *psl = psl_builtin();
    if (!psl) return 1;
    if (!psl_is_public_suffix(psl, "com")) return 2;
    if (psl_is_cookie_domain_acceptable(psl, "example.com", "com")) return 3;
    const char *domain = psl_registrable_domain(psl, "www.example.com");
    return !domain || strcmp(domain, "example.com");
}
