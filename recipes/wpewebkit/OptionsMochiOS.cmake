# Native development port. Keep OS identity, address-space rights and process
# launch authorization in the normal mochiOS/SDK interfaces.
if (NOT CMAKE_CROSSCOMPILING OR NOT CMAKE_SYSROOT)
    message(FATAL_ERROR "The mochiOS WPE port requires the real cross SDK")
endif ()
include(CheckCSourceCompiles)
# A header-only/static-library try_compile cannot detect missing runtime exports.
# This is a link check, never execution of a target binary on the host.
if (DEFINED CMAKE_TRY_COMPILE_TARGET_TYPE)
    set(_mochios_saved_try_compile_target_type "${CMAKE_TRY_COMPILE_TARGET_TYPE}")
else ()
    unset(_mochios_saved_try_compile_target_type)
endif ()
set(CMAKE_TRY_COMPILE_TARGET_TYPE EXECUTABLE)
check_c_source_compiles("
#if !defined(__mochios__) || defined(__linux__)
#error Not a native mochiOS compiler
#endif
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <link.h>
#include <pthread.h>
#include <sys/file.h>
#include <mochios/process.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>
extern const char *getexecname(void);
_Static_assert(_Generic(&write, ssize_t (*)(int, const void *, size_t): 1, default: 0), \"write ABI\");
_Static_assert(_Generic(&read, ssize_t (*)(int, void *, size_t): 1, default: 0), \"read ABI\");
static int visit(struct dl_phdr_info *info, size_t size, void *context) { return 0; }
int main(void) {
    pthread_attr_t attr;
    int e = pthread_getattr_np(pthread_self(), &attr);
    int fd = shm_open(\"/webkit-sdk-contract\", 0, 0600);
    e += ftruncate(fd, 4096);
    e += sendmsg(-1, (const struct msghdr *)0, 0);
    struct mochios_process_memory_usage usage;
    e += mochios_process_memory_read(&usage, sizeof(usage));
    unsigned char resident;
    Dl_info object;
    struct sched_param scheduling;
    int policy;
    e += flock(-1, LOCK_EX | LOCK_NB);
    e += mincore((void *)4096, 4096, &resident);
    e += madvise((void *)4096, 4096, MADV_DONTNEED);
    e += dladdr((void *)&main, &object);
    e += dl_iterate_phdr(visit, 0);
    e += pthread_getschedparam(pthread_self(), &policy, &scheduling);
    e += pthread_setschedparam(pthread_self(), SCHED_OTHER, &scheduling);
    return e + (getexecname() == 0);
}" MOCHIOS_NATIVE_WPE_UNIX_ABI)
if (DEFINED _mochios_saved_try_compile_target_type)
    set(CMAKE_TRY_COMPILE_TARGET_TYPE "${_mochios_saved_try_compile_target_type}")
else ()
    unset(CMAKE_TRY_COMPILE_TARGET_TYPE)
endif ()
unset(_mochios_saved_try_compile_target_type)
if (NOT MOCHIOS_NATIVE_WPE_UNIX_ABI)
    message(FATAL_ERROR "WPE requires a matching native SDK with ssize_t I/O ABI, flock, mincore, madvise, loader inspection, pthread scheduling, stack/accounting, shared-memory and descriptor-passing implementations")
endif ()
# Expose the SDK's GNU-compatible pthread extension without pretending to be
# glibc/Linux. No target code is executed by the configuration probe above.
add_compile_definitions(_GNU_SOURCE=1)
