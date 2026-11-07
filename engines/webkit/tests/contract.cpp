#include "mochios/webkit.h"
#include <type_traits>
static_assert(std::is_standard_layout_v<mwk_api>);
static_assert(std::is_standard_layout_v<mwk_frame>);
static_assert(sizeof(mwk_status)==4);
static_assert(offsetof(mwk_api,features)==8);
static_assert(MWK_API_V1_SIZE==sizeof(mwk_api));
