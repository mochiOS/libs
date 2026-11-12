#pragma once
#include "mochios/webkit.h"
#include <wpe/WPEProcessManager.h>
#include <stdint.h>

// The embedder owns all executable authority. WPE never receives a pathname.
struct _MwkProcessManager { WPEProcessManager parent; mwk_context_options options; };
struct _MwkProcessManagerClass { WPEProcessManagerClass parent; };
using MwkProcessManager = _MwkProcessManager;
using MwkProcessManagerClass = _MwkProcessManagerClass;
G_DEFINE_TYPE(MwkProcessManager, mwk_process_manager, WPE_TYPE_PROCESS_MANAGER)
static guint64 launch_helper(WPEProcessManager* manager, WPEProcessLaunchOptions* options, GError** error) {
    auto* self = reinterpret_cast<MwkProcessManager*>(manager);
    uint32_t kind = 0;
    switch (wpe_process_launch_options_get_process_type(options)) {
    case WPE_PROCESS_TYPE_WEB: kind = MWK_PROCESS_WEB; break;
    case WPE_PROCESS_TYPE_NETWORK: kind = MWK_PROCESS_NETWORK; break;
    default: break;
    }
    mwk_process_request request{sizeof(request), kind,
        wpe_process_launch_options_get_process_id(options),
        wpe_process_launch_options_get_ipc_socket_fd(options), 0};
    int32_t pid = 0;
    if (kind && self->options.launch(self->options.process_user, &request, &pid) == MWK_OK && pid > 0)
        return static_cast<guint64>(pid);
    g_set_error_literal(error, WPE_PROCESS_MANAGER_ERROR, WPE_PROCESS_MANAGER_ERROR_LAUNCH_FAILED,
        "The host could not start a signed WebKit helper");
    return 0;
}
static void terminate_helper(WPEProcessManager* manager, guint64 pid) {
    auto* self = reinterpret_cast<MwkProcessManager*>(manager);
    if (pid && pid <= INT32_MAX)
        self->options.terminate(self->options.process_user, static_cast<int32_t>(pid));
}
static void mwk_process_manager_class_init(MwkProcessManagerClass* klass) {
    auto* parent = WPE_PROCESS_MANAGER_CLASS(klass);
    parent->launch = launch_helper; parent->terminate = terminate_helper;
}
static void mwk_process_manager_init(MwkProcessManager*) { }
