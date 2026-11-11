#ifndef MOCHIOS_WEBKIT_H
#define MOCHIOS_WEBKIT_H
/** mochiOS WebKit C ABI, proposed major 1. No engine implementation is implied
 * by this header. C++/WPE types never cross this boundary. All objects belong to
 * their creating UI thread and application session. No API grants Capability.
 * All calls are nonblocking; dispatch performs bounded event-loop work, never
 * paint. Inputs are borrowed only for a call; queued operations copy them.
 * Callback data is borrowed until callback return; clients copy retained data.
 * Callbacks must not reenter or destroy engine objects. Unsupported features
 * return MWK_UNSUPPORTED; accepting a command is not a navigation success.
 */
#include <stddef.h>
#include <stdint.h>
#include <float.h>
#ifdef __cplusplus
extern "C" {
#endif
#define MWK_ABI_MAJOR 1u
#define MWK_FEATURE_RGBA_FRAME UINT64_C(1)
#define MWK_FEATURE_ZOOM UINT64_C(2)
#define MWK_FEATURE_IME UINT64_C(4)
#define MWK_INPUT_POINTER_MOVE 1u
#define MWK_INPUT_POINTER_PRESS 2u
#define MWK_INPUT_POINTER_RELEASE 3u
#define MWK_INPUT_SCROLL 4u
#define MWK_INPUT_KEY_PRESS 5u
#define MWK_INPUT_KEY_RELEASE 6u
#define MWK_INPUT_TEXT 7u
#define MWK_INPUT_IME_PREEDIT 8u
#define MWK_INPUT_IME_COMMIT 9u
#define MWK_INPUT_FOCUS 10u
#define MWK_OK 0u
#define MWK_INVALID_ARGUMENT 1u
#define MWK_UNSUPPORTED 2u
#define MWK_UNAVAILABLE 3u
#define MWK_WRONG_THREAD 4u
#define MWK_BUSY 5u
#define MWK_ENGINE_ERROR 6u
#define MWK_ABI_MISMATCH 7u
#define MWK_EVENT_STARTED 1u
#define MWK_EVENT_COMMITTED 2u
#define MWK_EVENT_FINISHED 3u
#define MWK_EVENT_FAILED 4u
#define MWK_EVENT_FRAME_AVAILABLE 5u
#define MWK_EVENT_TITLE_CHANGED 6u
#define MWK_EVENT_PROCESS_EXITED 7u
#define MWK_COMMAND_NAVIGATE 1u
#define MWK_COMMAND_BACK 2u
#define MWK_COMMAND_FORWARD 3u
#define MWK_COMMAND_RELOAD 4u
#define MWK_COMMAND_STOP 5u
#define MWK_COMMAND_ZOOM 6u
/** Trusted host-supplied UTF-8 HTML, loaded with an opaque about:blank origin. */
#define MWK_COMMAND_LOAD_HTML 7u
#define MWK_HISTORY_CAN_BACK 1u
#define MWK_HISTORY_CAN_FORWARD 2u
/** Current history item originated from trusted host HTML, not a remote URI. */
#define MWK_HISTORY_HOST_DOCUMENT 4u
#define MWK_PIXEL_RGBA8_PREMULTIPLIED 1u
/** Stable 32-bit status. Nonzero always represents a real rejected/failed call. */
typedef uint32_t mwk_status;
typedef struct mwk_context mwk_context;
typedef struct mwk_view mwk_view;
typedef struct mwk_frame_ref mwk_frame_ref;
/** UTF-8 bytes, no implicit NUL terminator; NULL is allowed only for length 0. */
typedef struct { const uint8_t *data; size_t length; } mwk_string;
/** Engine-reported event. IDs remain those of the original operation, including
 * redirects and late completions. Transport security must not be inferred from
 * the URI. No unvalidated certificate/security assertion exists in this ABI. */
typedef struct {
    uint32_t struct_size, type;
    uint64_t view_id, navigation_id;
    mwk_status status;
    /** History availability flags (MWK_HISTORY_*); remaining bits are zero. */
    uint32_t reserved;
    mwk_string text;
} mwk_event;
/** Host receives wakeups to schedule dispatch on its owning UI thread. Wake
 * may be called from another thread and must not call the engine itself. */
typedef struct {
    uint32_t struct_size, reserved;
    void *user;
    void (*wake)(void *user);
    void (*event)(void *user, const mwk_event *event);
} mwk_context_options;
/** Physical-pixel frame with explicit ownership. Pixels stay immutable until
 * frame_release, and a retained frame remains valid after view destruction. */
typedef struct {
    uint32_t struct_size, format, width, height;
    size_t stride, length;
    const uint8_t *pixels;
    double scale;
    uint64_t serial;
    mwk_frame_ref *reference;
} mwk_frame;
/** Host navigation IDs are nonzero and never reused within a view. Commands
 * retain their original IDs. Callers receive asynchronous failure events too. */
typedef struct {
    uint32_t struct_size, type;
    uint64_t navigation_id;
    mwk_string text;
    double value;
} mwk_command;
/** Input in view-local logical coordinates; scroll deltas are logical pixels.
 * key is a USB HID keyboard usage, button is 1 left / 2 middle / 3 right.
 * modifiers: bit0 shift, bit1 control, bit2 alt, bit3 meta. Focus flags bit0 is
 * focused; remaining bits are reserved zero. Text and IME strings are UTF-8;
 * IME selection is a checked byte range, never an unchecked pointer. */
typedef struct {
    uint32_t struct_size, type, flags, modifiers;
    double x, y, delta_x, delta_y;
    uint32_t key, button;
    mwk_string text;
    uint32_t selection_start, selection_end;
} mwk_input;
/** Append-only function table. Additions require struct_size negotiation;
 * incompatible ownership, layouts or semantics require a new ABI major/SONAME.
 * Engine instances must advertise only features actually implemented. */
typedef struct {
    uint32_t struct_size, abi_major;
    uint64_t features;
    /** Create an isolated application session; failure leaves *output NULL. */
    mwk_status (*context_create)(const mwk_context_options *, mwk_context **output);
    /** Destroy only with no live views/frames or callbacks in flight; otherwise
     * return MWK_BUSY without waiting. Success quiesces all future callbacks. */
    mwk_status (*context_destroy)(mwk_context *);
    /** Process at most budget events without waiting. budget must be nonzero. */
    mwk_status (*dispatch)(mwk_context *, uint32_t budget);
    /** Create a view with a caller's unique nonzero ID; failure clears output. */
    mwk_status (*view_create)(mwk_context *, uint64_t view_id, mwk_view **output);
    /** Stop new events for this view, preserving previously acquired frames. */
    mwk_status (*view_destroy)(mwk_view *);
    /** Enqueue a command, copying its input before returning. */
    mwk_status (*command)(mwk_view *, const mwk_command *);
    /** Update physical size and positive finite display scale, outside paint. */
    mwk_status (*resize)(mwk_view *, uint32_t width, uint32_t height, double scale);
    /** Copy and enqueue input on the UI thread. Unsupported IME is explicit. */
    mwk_status (*input)(mwk_view *, const mwk_input *);
    /** Acquire the latest completed frame. No rendering or waiting here.
     * MWK_UNAVAILABLE means no completed frame, never a fabricated blank page. */
    mwk_status (*frame_acquire)(mwk_view *, mwk_frame *output);
    /** Release exactly one acquired reference on its owning session thread. */
    mwk_status (*frame_release)(mwk_context *, mwk_frame_ref *);
} mwk_api;
/** Required major-1 prefix. Function-table additions may increase struct_size. */
#define MWK_API_V1_SIZE (offsetof(mwk_api, frame_release) + sizeof(((mwk_api *)0)->frame_release))
/** Reject truncated tables, wrong ABI and missing required operations. */
static inline int mwk_api_is_compatible(const mwk_api *api) {
    return api && api->struct_size >= MWK_API_V1_SIZE && api->abi_major == MWK_ABI_MAJOR &&
        api->context_create && api->context_destroy && api->dispatch && api->view_create &&
        api->view_destroy && api->command && api->resize && api->input &&
        (!(api->features & MWK_FEATURE_RGBA_FRAME) || (api->frame_acquire && api->frame_release));
}
/** Validate a borrowed RGBA frame before copying/painting it; no allocation/I/O. */
static inline int mwk_frame_is_valid(const mwk_frame *frame) {
    if (!frame || frame->struct_size < sizeof(mwk_frame) ||
        frame->format != MWK_PIXEL_RGBA8_PREMULTIPLIED || !frame->width || !frame->height ||
        !frame->pixels || !frame->reference || !(frame->scale > 0 && frame->scale <= DBL_MAX)) return 0;
    if ((size_t)frame->width > SIZE_MAX / 4) return 0;
    return frame->stride >= (size_t)frame->width * 4 &&
        frame->stride <= SIZE_MAX / frame->height && frame->length >= frame->stride * frame->height;
}
/** Sole exported entry point. No exception crosses this boundary. Unsupported
 * major/size fails and clears *output. On success the table is library-owned
 * and immutable until unload. The library must outlive every engine object. */
#if defined(__GNUC__)
__attribute__((visibility("default")))
#endif
mwk_status mwk_get_api(uint32_t major, size_t caller_size, const mwk_api **output);
#ifdef __cplusplus
}
#endif
#endif
