#include "mochios/webkit.h"
int main(void) { const mwk_api *api = 0; return mwk_get_api(MWK_ABI_MAJOR, MWK_API_V1_SIZE, &api) || !mwk_api_is_compatible(api); }
