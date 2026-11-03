#include <webp/decode.h>
#include <webp/encode.h>
#include <webp/demux.h>
#include <webp/mux.h>
int main(void) {
    const unsigned char pixel[4] = {32, 64, 128, 255};
    unsigned char *encoded = 0;
    size_t size = WebPEncodeLosslessRGBA(pixel, 1, 1, 4, &encoded);
    if (!size) return 1;
    int width = 0, height = 0;
    unsigned char *decoded = WebPDecodeRGBA(encoded, size, &width, &height);
    WebPMux *mux = WebPMuxNew();
    if (mux) WebPMuxDelete(mux);
    WebPData data = {encoded, size};
    WebPDemuxer *demux = WebPDemux(&data);
    if (demux) WebPDemuxDelete(demux);
    int failed = !decoded || width != 1 || height != 1;
    WebPFree(decoded); WebPFree(encoded);
    return failed;
}
