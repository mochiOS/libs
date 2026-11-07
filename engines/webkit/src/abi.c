#include "mochios/webkit.h"
/* A production link must supply the real platform implementation. Hidden
 * linkage prevents an unimplemented factory from becoming a runtime import. */
#if defined(__GNUC__)
__attribute__((visibility("hidden")))
#endif
const mwk_api *mwk_backend_query(void);

mwk_status mwk_get_api(uint32_t major, size_t caller_size, const mwk_api **output) {
    if (!output) return MWK_INVALID_ARGUMENT;
    *output = NULL;
    if (major != MWK_ABI_MAJOR || caller_size < MWK_API_V1_SIZE) return MWK_ABI_MISMATCH;
    const mwk_api *api = mwk_backend_query();
    if (!api) return MWK_UNAVAILABLE;
    if (!mwk_api_is_compatible(api)) return MWK_ENGINE_ERROR;
    *output = api;
    return MWK_OK;
}
