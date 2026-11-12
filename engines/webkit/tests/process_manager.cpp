// Host contract fixture: real upstream declarations and GObject dispatch,
// deliberately no WebKit renderer, no target executable launch.
#include "../src/process_manager.h"
#include <cassert>
struct _WPEProcessLaunchOptions { WPEProcessType type; guint64 id; int fd; };
G_DEFINE_TYPE(WPEProcessManager, wpe_process_manager, G_TYPE_OBJECT)
static void wpe_process_manager_class_init(WPEProcessManagerClass*) { }
static void wpe_process_manager_init(WPEProcessManager*) { }
WPEProcessType wpe_process_launch_options_get_process_type(WPEProcessLaunchOptions* o) { return o->type; }
guint64 wpe_process_launch_options_get_process_id(WPEProcessLaunchOptions* o) { return o->id; }
int wpe_process_launch_options_get_ipc_socket_fd(WPEProcessLaunchOptions* o) { return o->fd; }
GQuark wpe_process_manager_error_quark() { return g_quark_from_static_string("test-process-manager"); }
struct Host { unsigned calls = 0; mwk_status status = MWK_OK; int32_t pid = 42; uint32_t kind = 0; int32_t terminated = 0; };
static mwk_status launch(void* user, const mwk_process_request* r, int32_t* pid) {
    auto* host = static_cast<Host*>(user); ++host->calls;
    assert(r->struct_size == sizeof(*r) && r->identifier == 19 && r->ipc_socket_fd == 7 && !r->reserved);
    host->kind = r->type; *pid = host->pid; return host->status;
}
static mwk_status terminate(void* user, int32_t pid) {
    static_cast<Host*>(user)->terminated = pid; return MWK_OK;
}
int main() {
    Host host;
    auto* manager = static_cast<MwkProcessManager*>(g_object_new(mwk_process_manager_get_type(), nullptr));
    manager->options.process_user = &host; manager->options.launch = launch; manager->options.terminate = terminate;
    auto* parent = WPE_PROCESS_MANAGER(manager); auto* klass = WPE_PROCESS_MANAGER_GET_CLASS(parent);
    WPEProcessLaunchOptions request{WPE_PROCESS_TYPE_WEB, 19, 7}; GError* error = nullptr;
    assert(klass->launch(parent, &request, &error) == 42 && !error && host.kind == MWK_PROCESS_WEB);
    request.type = WPE_PROCESS_TYPE_NETWORK;
    assert(klass->launch(parent, &request, &error) == 42 && host.kind == MWK_PROCESS_NETWORK);
    request.type = WPE_PROCESS_TYPE_GPU;
    assert(klass->launch(parent, &request, &error) == 0 && error && host.calls == 2); g_clear_error(&error);
    request.type = WPE_PROCESS_TYPE_WEB; host.status = MWK_ENGINE_ERROR;
    assert(klass->launch(parent, &request, &error) == 0 && error); g_clear_error(&error);
    host.status = MWK_OK; host.pid = -1;
    assert(klass->launch(parent, &request, &error) == 0 && error); g_clear_error(&error);
    klass->terminate(parent, 42); assert(host.terminated == 42);
    klass->terminate(parent, 0); klass->terminate(parent, UINT64_MAX); assert(host.terminated == 42);
    g_object_unref(manager);
}
