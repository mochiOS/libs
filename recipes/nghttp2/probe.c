#include <nghttp2/nghttp2.h>
#include <stdint.h>
int main(void) {
    nghttp2_session_callbacks *callbacks = 0;
    nghttp2_session *session = 0;
    if (nghttp2_session_callbacks_new(&callbacks)) return 1;
    int status = nghttp2_session_client_new(&session, callbacks, 0);
    nghttp2_session_callbacks_del(callbacks);
    if (status) return 2;
    nghttp2_settings_entry setting = {NGHTTP2_SETTINGS_ENABLE_PUSH, 0};
    status = nghttp2_submit_settings(session, NGHTTP2_FLAG_NONE, &setting, 1);
    const uint8_t *data = 0;
    nghttp2_ssize size = status ? -1 : nghttp2_session_mem_send2(session, &data);
    nghttp2_session_del(session);
    return size <= 0 || !nghttp2_version(0);
}
