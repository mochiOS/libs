/* Development regression: real target GLib/GIO, not a spawn API substitute. */
#include <gio/gio.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define TARGET "/system/bin/gsubprocess-smoke"
static int failures;
static void result(const char *name, gboolean ok, GError *error) {
    printf("GSUBPROCESS %s mode=%s\n", ok ? "PASS" : "FAIL", name);
    if (error) fprintf(stderr, "GSUBPROCESS detail domain=%s code=%d message=%s\n",
                       g_quark_to_string(error->domain), error->code, error->message);
    failures += !ok;
    g_clear_error(&error);
}
static int child(int argc, char **argv) {
    if (!strcmp(argv[1], "--child-zero")) return 0;
    if (!strcmp(argv[1], "--child-seven")) return 7;
    if (!strcmp(argv[1], "--child-block")) {
        char byte;
        ssize_t n;
        do n = read(STDIN_FILENO, &byte, 1); while (n < 0 && errno == EINTR);
        return n == 0 ? 0 : 98;
    }
    if (strcmp(argv[1], "--child-echo") || argc != 4) return 90;
    char cwd[256];
    const char *token = getenv("GSUBPROCESS_TOKEN");
    if (!getcwd(cwd, sizeof(cwd)) || !token) return 91;
    printf("argv=%s|%s\nenv=%s\ncwd=%s\n", argv[2], argv[3], token, cwd);
    fflush(stdout);
    char bytes[4096];
    for (;;) {
        ssize_t n = read(STDIN_FILENO, bytes, sizeof(bytes));
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) return 92;
        if (!n) break;
        for (ssize_t at = 0; at < n;) {
            ssize_t written = write(STDOUT_FILENO, bytes + at, (size_t)(n - at));
            if (written < 0 && errno == EINTR) continue;
            if (written <= 0) return 93;
            at += written;
        }
    }
    return write(STDERR_FILENO, "child-stderr\n", 13) == 13 ? 0 : 94;
}
static GSubprocess *start(const char *mode, GSubprocessFlags flags, GError **error) {
    return g_subprocess_new(flags, error, TARGET, mode, NULL);
}
static void roundtrip(void) {
    GError *error = NULL;
    GSubprocessLauncher *launcher = g_subprocess_launcher_new(
        G_SUBPROCESS_FLAGS_STDIN_PIPE | G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_PIPE);
    g_subprocess_launcher_setenv(launcher, "GSUBPROCESS_TOKEN", "kept value", TRUE);
    g_subprocess_launcher_set_cwd(launcher, "/tmp");
    GSubprocess *process = g_subprocess_launcher_spawn(launcher, &error,
        TARGET, "--child-echo", "two words", "--literal", NULL);
    g_object_unref(launcher);
    char *output = NULL, *errors = NULL;
    gboolean ok = process && g_subprocess_communicate_utf8(process, "first line\nsecond line\n",
        NULL, &output, &errors, &error);
    ok = ok && g_subprocess_get_successful(process) &&
        !g_strcmp0(output, "argv=two words|--literal\nenv=kept value\ncwd=/tmp\nfirst line\nsecond line\n") &&
        !g_strcmp0(errors, "child-stderr\n");
    result("pipes-argv-env-cwd", ok, error);
    g_free(output); g_free(errors); g_clear_object(&process);
}
static void nonzero(void) {
    GError *error = NULL;
    GSubprocess *process = start("--child-seven", G_SUBPROCESS_FLAGS_NONE, &error);
    gboolean waited = process && g_subprocess_wait_check(process, NULL, &error);
    gboolean ok = process && !waited && g_error_matches(error, G_SPAWN_EXIT_ERROR, 7) &&
        g_subprocess_get_if_exited(process) && g_subprocess_get_exit_status(process) == 7;
    if (ok) g_clear_error(&error);
    result("nonzero-exit", ok, error);
    g_clear_object(&process);
}
static void path_search(void) {
    GError *error = NULL;
    GSubprocess *process = g_subprocess_new(G_SUBPROCESS_FLAGS_NONE, &error,
        "gsubprocess-smoke", "--child-zero", NULL);
    gboolean ok = process && g_subprocess_wait_check(process, NULL, &error);
    result("path-search", ok, error);
    g_clear_object(&process);
}
typedef struct {
    GMainLoop *loop;
    GCancellable *cancel;
    GSubprocess *process;
    gboolean done, ok, timed_out, expect_cancel;
    GError *error;
} AsyncWait;
static void waited(GObject *object, GAsyncResult *async, gpointer data) {
    AsyncWait *wait = data;
    gboolean ok = g_subprocess_wait_finish(G_SUBPROCESS(object), async, &wait->error);
    wait->ok = wait->expect_cancel ? (!ok && g_error_matches(wait->error, G_IO_ERROR, G_IO_ERROR_CANCELLED)) : ok;
    if (wait->ok && wait->expect_cancel) g_clear_error(&wait->error);
    wait->done = TRUE;
    g_main_loop_quit(wait->loop);
}
static gboolean timeout_wait(gpointer data) {
    AsyncWait *wait = data;
    wait->timed_out = TRUE;
    g_cancellable_cancel(wait->cancel);
    return G_SOURCE_REMOVE;
}
static void asynchronous(gboolean cancel) {
    AsyncWait wait = {0};
    wait.expect_cancel = cancel;
    wait.cancel = g_cancellable_new();
    wait.loop = g_main_loop_new(NULL, FALSE);
    wait.process = start(cancel ? "--child-block" : "--child-zero",
                         cancel ? G_SUBPROCESS_FLAGS_STDIN_PIPE : G_SUBPROCESS_FLAGS_NONE, &wait.error);
    if (wait.process) {
        guint timeout = g_timeout_add_seconds(30, timeout_wait, &wait);
        g_subprocess_wait_async(wait.process, wait.cancel, waited, &wait);
        if (cancel) g_cancellable_cancel(wait.cancel);
        if (!wait.done) g_main_loop_run(wait.loop);
        if (!wait.timed_out) g_source_remove(timeout);
        if (cancel) {
            /* Cancelling the wait must not kill the child: EOF lets it exit 0. */
            gboolean closed = g_output_stream_close(g_subprocess_get_stdin_pipe(wait.process), NULL, NULL);
            wait.ok = wait.ok && closed && g_subprocess_wait_check(wait.process, NULL, &wait.error);
        } else if (wait.done && wait.ok) {
            wait.ok = g_subprocess_get_successful(wait.process);
        }
    }
    result(cancel ? "cancelled-wait" : "async-wait",
           wait.process && wait.done && wait.ok && !wait.timed_out, wait.error);
    g_clear_object(&wait.process); g_main_loop_unref(wait.loop); g_object_unref(wait.cancel);
}
static void missing(void) {
    GError *error = NULL;
    GSubprocess *process = g_subprocess_new(G_SUBPROCESS_FLAGS_NONE, &error,
        "/system/bin/gsubprocess-deliberately-missing", NULL);
    gboolean ok = !process && g_error_matches(error, G_SPAWN_ERROR, G_SPAWN_ERROR_NOENT);
    if (ok) g_clear_error(&error);
    result("missing-executable", ok, error);
    g_clear_object(&process);
}
int main(int argc, char **argv) {
    if (argc > 1 && !strncmp(argv[1], "--child-", 8)) return child(argc, argv);
    if (!freopen("/tmp/gsubprocess-results.log", "a", stdout) ||
        !freopen("/tmp/gsubprocess-results.log", "a", stderr)) return 2;
    setvbuf(stdout, NULL, _IONBF, 0); setvbuf(stderr, NULL, _IONBF, 0);
    puts("GSUBPROCESS START");
    roundtrip(); nonzero(); path_search(); asynchronous(FALSE); asynchronous(TRUE); missing();
    printf("GSUBPROCESS COMPLETE failures=%d\n", failures);
    return failures ? 1 : 0;
}
