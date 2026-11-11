// Native WPE implementation of the provisional mochiOS WebKit ABI.
// Uses real WebKit snapshots; it never fabricates pixels or load success.
#include "mochios/webkit.h"
#include <wpe/webkit.h>
#include <wpe/wpe-platform.h>
#include <wpe/headless/wpe-headless.h>
#include <pthread.h>
#include <cmath>
#include <deque>
#include <memory>
#include <string>
#include <vector>
#include <algorithm>
#include <new>
#include <cstring>
#include <atomic>

struct mwk_frame_ref { std::shared_ptr<std::vector<uint8_t>> pixels; uint32_t width, height; uint64_t serial; double scale; };
struct Pending { uint32_t type; uint64_t id; std::string text; double value; };
struct mwk_view;
struct mwk_context {
    mwk_context_options options;
    pthread_t owner = pthread_self();
    GMainContext* loop = g_main_context_new();
    std::vector<mwk_view*> views;
    std::vector<mwk_frame_ref*> leases;
    bool dispatching = false; unsigned snapshots = 0;
};
struct mwk_view {
    mwk_context* context; uint64_t id, navigation = 0, highest = 0;
    WebKitWebView* web = nullptr;
    std::deque<Pending> pending;
    std::unique_ptr<mwk_frame_ref> latest;
    bool loading = false, failed = false, dirty = false, snapshot = false, committed = false, hostHTML = false;
    uint32_t width = 800, height = 600; double scale = 1; uint64_t serial = 0;
    GCancellable* cancellation = nullptr;
    // An outstanding asynchronous snapshot owns a reference after view_destroy.
    unsigned references = 1;
    ~mwk_view() {
        if (web) {
            g_signal_handlers_disconnect_by_data(webkit_web_view_get_wpe_view(web), this);
            g_signal_handlers_disconnect_by_data(web, this);
            g_object_unref(web);
        }
        if (cancellation) g_object_unref(cancellation);
    }
};
struct LoopScope {
    GMainContext* loop;
    explicit LoopScope(mwk_context* c): loop(c->loop) { g_main_context_push_thread_default(loop); }
    ~LoopScope() { g_main_context_pop_thread_default(loop); }
};
static bool owner(mwk_context* c) { return c && pthread_equal(c->owner, pthread_self()); }
static void emit(mwk_view* v, uint32_t type, const char* text = "", mwk_status status = MWK_OK) {
    if (!v->web || !v->context->options.event) return;
    mwk_event event{sizeof(event), type, v->id, v->navigation, status, 0,
        {reinterpret_cast<const uint8_t*>(text ? text : ""), text ? strlen(text) : 0}};
    // The provisional reserved word carries real back/forward availability.
    event.reserved = (webkit_web_view_can_go_back(v->web) ? 1u : 0u) | (webkit_web_view_can_go_forward(v->web) ? 2u : 0u);
    auto* item = webkit_back_forward_list_get_current_item(webkit_web_view_get_back_forward_list(v->web));
    if (item && g_object_get_data(G_OBJECT(item), "mochios-host-document")) event.reserved |= MWK_HISTORY_HOST_DOCUMENT;
    v->context->options.event(v->context->options.user, &event);
}
static void release_view(mwk_view* v) { if (!--v->references) delete v; }
static void snapshot_done(GObject* object, GAsyncResult* result, gpointer data) {
    auto* v = static_cast<mwk_view*>(data);
    GError* error = nullptr;
    WebKitImage* image = webkit_web_view_get_snapshot_finish(WEBKIT_WEB_VIEW(object), result, &error);
    v->snapshot = false; --v->context->snapshots;
    try {
    if (image && v->web) {
        auto w = webkit_image_get_width(image), h = webkit_image_get_height(image);
        auto stride = webkit_image_get_stride(image);
        gsize length = 0;
        auto* bytes = static_cast<const uint8_t*>(g_bytes_get_data(webkit_image_as_bytes(image), &length));
        if (w > 0 && h > 0 && w <= 8192 && h <= 8192 && static_cast<size_t>(w) * h <= 16*1024*1024 && stride >= static_cast<size_t>(w) * 4 && length >= static_cast<size_t>(stride) * h) {
            auto frame = std::make_unique<mwk_frame_ref>();
            frame->width = w; frame->height = h; frame->scale = v->scale; frame->serial = ++v->serial;
            frame->pixels = std::make_shared<std::vector<uint8_t>>(static_cast<size_t>(w) * h * 4);
            // WebKitImage uses premultiplied BGRA8 on the native x86_64 port.
            for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
                auto* src = bytes + static_cast<size_t>(y) * stride + x * 4;
                auto* dst = frame->pixels->data() + (static_cast<size_t>(y) * w + x) * 4;
                dst[0] = src[2]; dst[1] = src[1]; dst[2] = src[0]; dst[3] = src[3];
            }
            v->latest = std::move(frame); emit(v, MWK_EVENT_FRAME_AVAILABLE);
        } else emit(v, MWK_EVENT_FAILED, "Invalid WebKit snapshot dimensions", MWK_ENGINE_ERROR);
    } else if (v->web && error && !g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
        emit(v, MWK_EVENT_FAILED, error->message, MWK_ENGINE_ERROR);
    } catch (...) { if (v->web) emit(v, MWK_EVENT_FAILED, "Could not allocate a WebKit frame", MWK_ENGINE_ERROR); }
    if (image) g_object_unref(image);
    g_clear_error(&error); release_view(v);
}
static std::atomic<bool> session_claimed{false};
static mwk_status context_create(const mwk_context_options* options, mwk_context** output) {
    if (!output) return MWK_INVALID_ARGUMENT;
    *output = nullptr;
    if (!options || options->struct_size < sizeof(*options) || options->reserved) return MWK_INVALID_ARGUMENT;
    // WebKit has one UI RunLoop per process. Reject a second session explicitly.
    bool expected = false;
    if (!session_claimed.compare_exchange_strong(expected, true)) return MWK_BUSY;
    auto* c = new (std::nothrow) mwk_context;
    if (!c) { session_claimed = false; return MWK_ENGINE_ERROR; }
    c->options = *options; *output = c; return MWK_OK;
}
static mwk_status context_destroy(mwk_context* c) {
    if (!owner(c)) return MWK_WRONG_THREAD;
    if (c->dispatching || !c->views.empty() || !c->leases.empty() || c->snapshots) return MWK_BUSY;
    // Async cancellation callbacks still refer to this loop/context.
    if (g_main_context_pending(c->loop)) return MWK_BUSY;
    g_main_context_unref(c->loop); delete c; return MWK_OK;
}
static mwk_status view_create(mwk_context* c, uint64_t id, mwk_view** output) {
    if (!output) return MWK_INVALID_ARGUMENT;
    *output = nullptr;
    if (!owner(c)) return MWK_WRONG_THREAD;
    if (!id || c->dispatching || std::any_of(c->views.begin(),c->views.end(),[id](auto* v){return v->id == id;})) return MWK_INVALID_ARGUMENT;
    LoopScope scope(c);
    auto v = std::make_unique<mwk_view>(); v->context = c; v->id = id;
    auto* display = wpe_display_headless_new();
    GError* error = nullptr;
    if (!wpe_display_connect(display, &error)) { g_clear_error(&error); g_object_unref(display); return MWK_UNAVAILABLE; }
    auto* session = webkit_network_session_new_ephemeral();
    v->web = WEBKIT_WEB_VIEW(g_object_new(WEBKIT_TYPE_WEB_VIEW, "display", display, "network-session", session, nullptr));
    g_object_unref(session); g_object_unref(display);
    if (!v->web) return MWK_ENGINE_ERROR;
    v->cancellation = g_cancellable_new();
    g_signal_connect(v->web, "load-changed", G_CALLBACK(+[](WebKitWebView* web, WebKitLoadEvent event, mwk_view* v) {
        if (event == WEBKIT_LOAD_COMMITTED) {
            v->committed = true;
            if (v->hostHTML) {
                auto* item = webkit_back_forward_list_get_current_item(webkit_web_view_get_back_forward_list(web));
                if (item) g_object_set_data(G_OBJECT(item), "mochios-host-document", GINT_TO_POINTER(1));
            }
            emit(v, MWK_EVENT_COMMITTED, webkit_web_view_get_uri(web));
        }
        if (event == WEBKIT_LOAD_FINISHED) { v->loading = false; v->dirty = true; if (!v->failed) emit(v, MWK_EVENT_FINISHED); }
    }), v.get());
    g_signal_connect(v->web, "load-failed", G_CALLBACK(+[](WebKitWebView*, WebKitLoadEvent, const char*, GError* e, mwk_view* v)->gboolean {
        v->failed = true; emit(v, MWK_EVENT_FAILED, e->message, MWK_ENGINE_ERROR); return TRUE;
    }), v.get());
    g_signal_connect(v->web, "notify::title", G_CALLBACK(+[](WebKitWebView* web, GParamSpec*, mwk_view* v) { if (v->committed) emit(v, MWK_EVENT_TITLE_CHANGED, webkit_web_view_get_title(web)); }), v.get());
    g_signal_connect(v->web, "web-process-terminated", G_CALLBACK(+[](WebKitWebView*, WebKitWebProcessTerminationReason, mwk_view* v) {
        v->loading = false; v->failed = true; emit(v, MWK_EVENT_PROCESS_EXITED, "Web content process terminated", MWK_ENGINE_ERROR);
    }), v.get());
    auto* platform = webkit_web_view_get_wpe_view(v->web);
    g_signal_connect(platform, "buffer-rendered", G_CALLBACK(+[](WPEView*, WPEBuffer*, mwk_view* v) { v->dirty = true; }), v.get());
    wpe_toplevel_resize(wpe_view_get_toplevel(platform), 800, 600);
    c->views.push_back(v.get()); *output = v.release(); return MWK_OK;
}
static mwk_status view_destroy(mwk_view* v) {
    if (!v || !owner(v->context)) return MWK_WRONG_THREAD;
    if (v->context->dispatching) return MWK_BUSY;
    LoopScope scope(v->context);
    auto& views = v->context->views; views.erase(std::remove(views.begin(), views.end(), v), views.end());
    g_signal_handlers_disconnect_by_data(webkit_web_view_get_wpe_view(v->web), v);
    g_signal_handlers_disconnect_by_data(v->web, v);
    g_cancellable_cancel(v->cancellation); g_object_unref(v->cancellation); v->cancellation = nullptr;
    g_object_unref(v->web); v->web = nullptr; release_view(v); return MWK_OK;
}
static mwk_status command(mwk_view* v, const mwk_command* cmd) {
    if (!v || !owner(v->context)) return MWK_WRONG_THREAD;
    if (!cmd || cmd->struct_size < sizeof(*cmd) || (!cmd->text.data && cmd->text.length) || cmd->text.length > 1024*1024) return MWK_INVALID_ARGUMENT;
    if (v->pending.size() >= 64) return MWK_BUSY;
    if (cmd->type < MWK_COMMAND_NAVIGATE || cmd->type > MWK_COMMAND_LOAD_HTML) return MWK_UNSUPPORTED;
    if ((cmd->type <= MWK_COMMAND_RELOAD || cmd->type == MWK_COMMAND_LOAD_HTML)) {
        if (!cmd->navigation_id || cmd->navigation_id <= v->highest) return MWK_INVALID_ARGUMENT;
        v->highest = cmd->navigation_id;
    }
    std::string text(cmd->text.data ? reinterpret_cast<const char*>(cmd->text.data) : "",cmd->text.length);
    if (text.find('\0') != std::string::npos || !g_utf8_validate(text.data(),text.size(),nullptr)) return MWK_INVALID_ARGUMENT;
    // Navigations grant no arbitrary file:// access. HTML is supplied by the trusted host.
    if (cmd->type == MWK_COMMAND_NAVIGATE && !g_str_has_prefix(text.c_str(),"https://") && !g_str_has_prefix(text.c_str(),"http://")) return MWK_INVALID_ARGUMENT;
    if (cmd->type == MWK_COMMAND_ZOOM && (!std::isfinite(cmd->value) || cmd->value < .5 || cmd->value > 3)) return MWK_INVALID_ARGUMENT;
    v->pending.push_back({cmd->type,cmd->navigation_id,std::move(text),cmd->value}); return MWK_OK;
}
static mwk_status dispatch(mwk_context* c, uint32_t budget) {
    if (!owner(c)) return MWK_WRONG_THREAD;
    if (!budget || budget > 256) return MWK_INVALID_ARGUMENT;
    if (c->dispatching) return MWK_BUSY;
    LoopScope scope(c); c->dispatching = true;
    struct Reset { bool& flag; ~Reset() { flag = false; } } reset{c->dispatching};
    for (auto* v : c->views) {
        if (!v->pending.empty()) {
            auto& next = v->pending.front();
            if (v->loading && next.type != MWK_COMMAND_ZOOM) webkit_web_view_stop_loading(v->web);
            if (!v->loading || next.type == MWK_COMMAND_ZOOM) {
                auto op = std::move(next); v->pending.pop_front();
                if (op.type <= MWK_COMMAND_RELOAD || op.type == MWK_COMMAND_LOAD_HTML) { v->navigation = op.id; v->failed = false; v->committed = false; v->hostHTML = op.type == MWK_COMMAND_LOAD_HTML; v->loading = true; }
                switch (op.type) {
                case MWK_COMMAND_NAVIGATE: webkit_web_view_load_uri(v->web, op.text.c_str()); break;
                case MWK_COMMAND_LOAD_HTML: webkit_web_view_load_html(v->web, op.text.c_str(), "about:blank"); break;
                case MWK_COMMAND_BACK: if (webkit_web_view_can_go_back(v->web)) webkit_web_view_go_back(v->web); else { v->loading = false; emit(v, MWK_EVENT_FAILED, "No previous page", MWK_UNAVAILABLE); } break;
                case MWK_COMMAND_FORWARD: if (webkit_web_view_can_go_forward(v->web)) webkit_web_view_go_forward(v->web); else { v->loading = false; emit(v, MWK_EVENT_FAILED, "No next page", MWK_UNAVAILABLE); } break;
                case MWK_COMMAND_RELOAD: webkit_web_view_reload(v->web); break;
                case MWK_COMMAND_STOP: webkit_web_view_stop_loading(v->web); break;
                case MWK_COMMAND_ZOOM: webkit_web_view_set_zoom_level(v->web, op.value); break;
                }
            }
        }
        if (v->dirty && !v->snapshot && v->navigation) {
            v->dirty = false; v->snapshot = true; ++v->references; ++c->snapshots;
            webkit_web_view_get_snapshot(v->web, WEBKIT_SNAPSHOT_REGION_VISIBLE, WEBKIT_SNAPSHOT_OPTIONS_NONE, v->cancellation, snapshot_done, v);
        }
    }
    for (uint32_t i = 0; i < budget && g_main_context_pending(c->loop); ++i) g_main_context_iteration(c->loop,FALSE);
    c->dispatching = false; return MWK_OK;
}
static mwk_status resize(mwk_view* v, uint32_t w, uint32_t h, double scale) {
    if (!v || !owner(v->context)) return MWK_WRONG_THREAD;
    if (!w || !h || w > 8192 || h > 8192 || !std::isfinite(scale) || scale != 1) return MWK_INVALID_ARGUMENT;
    LoopScope scope(v->context); v->width = w; v->height = h; v->scale = scale;
    wpe_toplevel_resize(wpe_view_get_toplevel(webkit_web_view_get_wpe_view(v->web)),w,h); v->dirty = true; return MWK_OK;
}
static mwk_status input(mwk_view* v, const mwk_input* in) {
    if (!v || !owner(v->context)) return MWK_WRONG_THREAD;
    if (!in || in->struct_size < sizeof(*in) || (in->modifiers & ~15u) || (in->flags & ~(in->type == MWK_INPUT_FOCUS ? 1u : 0u))) return MWK_INVALID_ARGUMENT;
    if (!std::isfinite(in->x) || !std::isfinite(in->y) || !std::isfinite(in->delta_x) || !std::isfinite(in->delta_y)) return MWK_INVALID_ARGUMENT;
    LoopScope scope(v->context);
    auto* view = webkit_web_view_get_wpe_view(v->web);
    auto time = static_cast<uint32_t>(g_get_monotonic_time()/1000);
    auto mods = static_cast<WPEModifiers>(((in->modifiers & 1) ? WPE_MODIFIER_KEYBOARD_SHIFT : 0) | ((in->modifiers & 2) ? WPE_MODIFIER_KEYBOARD_CONTROL : 0) | ((in->modifiers & 4) ? WPE_MODIFIER_KEYBOARD_ALT : 0) | ((in->modifiers & 8) ? WPE_MODIFIER_KEYBOARD_META : 0));
    WPEEvent* event = nullptr;
    switch (in->type) {
    case MWK_INPUT_POINTER_MOVE: event = wpe_event_pointer_move_new(WPE_EVENT_POINTER_MOVE,view,WPE_INPUT_SOURCE_MOUSE,time,mods,in->x,in->y,0,0); break;
    case MWK_INPUT_POINTER_PRESS: case MWK_INPUT_POINTER_RELEASE:
        event = wpe_event_pointer_button_new(in->type == MWK_INPUT_POINTER_PRESS ? WPE_EVENT_POINTER_DOWN : WPE_EVENT_POINTER_UP,view,WPE_INPUT_SOURCE_MOUSE,time,mods,in->button,in->x,in->y,1); break;
    case MWK_INPUT_SCROLL: event = wpe_event_scroll_new(view,WPE_INPUT_SOURCE_MOUSE,time,mods,in->delta_x,in->delta_y,TRUE,FALSE,in->x,in->y); break;
    case MWK_INPUT_FOCUS: if (in->flags & 1) wpe_view_focus_in(view); else wpe_view_focus_out(view); return MWK_OK;
    case MWK_INPUT_KEY_PRESS: case MWK_INPUT_KEY_RELEASE: {
        guint keyval = 0;
        switch (in->key) {
        case 40: keyval=0xff0d; break; case 41:keyval=0xff1b;break; case 42:keyval=0xff08;break;
        case 43:keyval=0xff09;break; case 74:keyval=0xff50;break;case 75:keyval=0xff55;break;
        case 76:keyval=0xffff;break;case 77:keyval=0xff57;break;case 78:keyval=0xff56;break;
        case 79:keyval=0xff53;break;case 80:keyval=0xff51;break;case 81:keyval=0xff54;break;case 82:keyval=0xff52;break;
        default:return MWK_UNSUPPORTED;
        }
        event=wpe_event_keyboard_new(in->type==MWK_INPUT_KEY_PRESS?WPE_EVENT_KEYBOARD_KEY_DOWN:WPE_EVENT_KEYBOARD_KEY_UP,view,WPE_INPUT_SOURCE_KEYBOARD,time,mods,in->key,keyval);break;
    }
    case MWK_INPUT_TEXT: {
        if (!in->text.length) return MWK_OK;
        if ((!in->text.data && in->text.length) || in->text.length > 65536 || !g_utf8_validate(reinterpret_cast<const char*>(in->text.data),in->text.length,nullptr)) return MWK_INVALID_ARGUMENT;
        const auto* p=reinterpret_cast<const char*>(in->text.data); const auto* end=p+in->text.length;
        while (p<end) {
            gunichar ch=g_utf8_get_char(p); guint keyval=ch<256?ch:0x01000000|ch;
            for (auto type:{WPE_EVENT_KEYBOARD_KEY_DOWN,WPE_EVENT_KEYBOARD_KEY_UP}) {
                auto* key=wpe_event_keyboard_new(type,view,WPE_INPUT_SOURCE_KEYBOARD,time,mods,0,keyval);
                wpe_view_event(view,key);wpe_event_unref(key);
            }
            p=g_utf8_next_char(p);
        }
        return MWK_OK;
    }
    default: return MWK_UNSUPPORTED;
    }
    wpe_view_event(view,event); wpe_event_unref(event); return MWK_OK;
}
static mwk_status acquire(mwk_view* v, mwk_frame* out) {
    if (!v || !owner(v->context)) return MWK_WRONG_THREAD;
    if (!out || out->struct_size < sizeof(*out)) return MWK_INVALID_ARGUMENT;
    if (!v->latest) { *out = {}; return MWK_UNAVAILABLE; }
    if (v->context->leases.size() >= 64) return MWK_BUSY;
    auto frame = std::make_unique<mwk_frame_ref>(*v->latest); auto* ptr = frame.get();
    *out = {sizeof(*out),MWK_PIXEL_RGBA8_PREMULTIPLIED,ptr->width,ptr->height,static_cast<size_t>(ptr->width)*4,ptr->pixels->size(),ptr->pixels->data(),ptr->scale,ptr->serial,ptr};
    v->context->leases.push_back(frame.release()); return MWK_OK;
}
static mwk_status release(mwk_context* c, mwk_frame_ref* ref) {
    if (!owner(c)) return MWK_WRONG_THREAD;
    auto it = std::find(c->leases.begin(),c->leases.end(),ref);
    if (it == c->leases.end()) return MWK_INVALID_ARGUMENT;
    c->leases.erase(it); delete ref; return MWK_OK;
}
template<typename> struct Guard;
template<typename... A> struct Guard<mwk_status(*)(A...)> {
    template<mwk_status(*Function)(A...)> static mwk_status call(A... args) noexcept {
        try { return Function(args...); } catch (...) { return MWK_ENGINE_ERROR; }
    }
};
#define SAFE(F) Guard<decltype(&F)>::call<&F>
extern "C" __attribute__((visibility("hidden"))) const mwk_api* mwk_backend_query() {
    static const mwk_api api{sizeof(mwk_api),MWK_ABI_MAJOR,MWK_FEATURE_RGBA_FRAME | MWK_FEATURE_ZOOM,
        SAFE(context_create),SAFE(context_destroy),SAFE(dispatch),SAFE(view_create),SAFE(view_destroy),SAFE(command),SAFE(resize),SAFE(input),SAFE(acquire),SAFE(release)};
    return &api;
}
