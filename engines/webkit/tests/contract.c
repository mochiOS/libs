#include "mochios/webkit.h"
#include <assert.h>
#include <math.h>
#include <string.h>
static const mwk_api *selected;
const mwk_api *mwk_backend_query(void) { return selected; } /* Test fixture only. */
static mwk_status create(const mwk_context_options *o, mwk_context **p) {(void)o;(void)p;return MWK_UNAVAILABLE;}
static mwk_status context(mwk_context *p) {(void)p;return MWK_UNAVAILABLE;}
static mwk_status dispatch(mwk_context *p,uint32_t n) {(void)p;(void)n;return MWK_UNAVAILABLE;}
static mwk_status view(mwk_context *c,uint64_t n,mwk_view **v) {(void)c;(void)n;(void)v;return MWK_UNAVAILABLE;}
static mwk_status destroy(mwk_view *v) {(void)v;return MWK_UNAVAILABLE;}
static mwk_status command(mwk_view *v,const mwk_command *c) {(void)v;(void)c;return MWK_UNAVAILABLE;}
static mwk_status resize(mwk_view *v,uint32_t w,uint32_t h,double s) {(void)v;(void)w;(void)h;(void)s;return MWK_UNAVAILABLE;}
static mwk_status input(mwk_view *v,const mwk_input *i) {(void)v;(void)i;return MWK_UNAVAILABLE;}
int main(void) {
    const mwk_api *out=(const mwk_api *)(uintptr_t)1;
    assert(mwk_get_api(99,MWK_API_V1_SIZE,&out)==MWK_ABI_MISMATCH && out==NULL);
    assert(mwk_get_api(1,0,&out)==MWK_ABI_MISMATCH && out==NULL);
    assert(mwk_get_api(1,MWK_API_V1_SIZE,NULL)==MWK_INVALID_ARGUMENT);
    assert(mwk_get_api(1,MWK_API_V1_SIZE,&out)==MWK_UNAVAILABLE && out==NULL);
    mwk_api api={0};selected=&api;
    assert(mwk_get_api(1,MWK_API_V1_SIZE,&out)==MWK_ENGINE_ERROR && out==NULL);
    api=(mwk_api){sizeof(mwk_api),1,0,create,context,dispatch,view,destroy,command,resize,input,NULL,NULL};
    assert(mwk_get_api(1,MWK_API_V1_SIZE,&out)==MWK_OK && out==&api);
    api.features=MWK_FEATURE_RGBA_FRAME;
    assert(!mwk_api_is_compatible(&api));
    api.features=0;api.struct_size=MWK_API_V1_SIZE-1;
    assert(!mwk_api_is_compatible(&api));
    uint8_t pixels[16]={0};
    mwk_frame frame={sizeof(mwk_frame),MWK_PIXEL_RGBA8_PREMULTIPLIED,2,2,8,16,pixels,1.0,1,(mwk_frame_ref *)pixels};
    assert(mwk_frame_is_valid(&frame));
    frame.length=15;assert(!mwk_frame_is_valid(&frame));frame.length=16;
    frame.stride=SIZE_MAX;assert(!mwk_frame_is_valid(&frame));frame.stride=8;
    frame.scale=NAN;assert(!mwk_frame_is_valid(&frame));
    frame.scale=INFINITY;assert(!mwk_frame_is_valid(&frame));
    frame.scale=0;assert(!mwk_frame_is_valid(&frame));
    return 0;
}
