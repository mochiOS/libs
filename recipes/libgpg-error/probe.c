#include <gpg-error.h>
#include <errno.h>
#include <stddef.h>
#include <string.h>
_Static_assert(sizeof(((gpgrt_lock_t *)0)->u._priv) == sizeof(pthread_mutex_t), "native mutex size");
_Static_assert(offsetof(gpgrt_lock_t, u) % _Alignof(pthread_mutex_t) == 0, "native mutex alignment");
int main(void) {
    gpgrt_lock_t lock = GPGRT_LOCK_INITIALIZER;
    pthread_mutex_t expected = PTHREAD_MUTEX_INITIALIZER;
    if (lock.u._native.state != expected.state || lock.u._native.type != expected.type ||
        lock.u._native.owner != expected.owner || lock.u._native.depth != expected.depth ||
        lock.u._native.initialized != expected.initialized ||
        lock.u._native.waiters != expected.waiters || lock.u._native.reserved != expected.reserved) return 1;
    if (!gpg_error_check_version(NULL)) return 1;
    if (gpgrt_lock_lock(&lock)) return 1;
    if (gpgrt_lock_unlock(&lock)) return 1;
    if (gpgrt_lock_destroy(&lock)) return 1;
    gpg_error_t error = gpg_error_from_errno(ENOENT);
    if (gpg_err_code(error) != GPG_ERR_ENOENT || !gpg_strerror(error)) return 1;
    return gpg_err_code_to_errno(GPG_ERR_ENOENT) != ENOENT;
}
