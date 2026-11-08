# Native development port. Keep OS identity, address-space rights and process
# launch authorization in the normal mochiOS/SDK interfaces.
if (NOT CMAKE_CROSSCOMPILING OR NOT CMAKE_SYSROOT)
    message(FATAL_ERROR "The mochiOS WPE port requires the real cross SDK")
endif ()
include(CheckCSourceCompiles)
check_c_source_compiles("
#if !defined(__mochios__) || defined(__linux__)
#error Not a native mochiOS compiler
#endif
#define _GNU_SOURCE 1
#include <pthread.h>
#include <mochios/process.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>
extern const char *getexecname(void);
int main(void) {
    pthread_attr_t attr;
    int e = pthread_getattr_np(pthread_self(), &attr);
    int fd = shm_open(\"/webkit-sdk-contract\", 0, 0600);
    e += ftruncate(fd, 4096);
    e += sendmsg(-1, (const struct msghdr *)0, 0);
    struct mochios_process_memory_usage usage;
    e += mochios_process_memory_read(&usage, sizeof(usage));
    return e + (getexecname() == 0);
}" MOCHIOS_NATIVE_WPE_HEADERS)
if (NOT MOCHIOS_NATIVE_WPE_HEADERS)
    message(FATAL_ERROR "WPE needs native pthread stack, resident memory, executable name, shared-memory and descriptor-passing SDK APIs")
endif ()
# Expose the SDK's GNU-compatible pthread extension without pretending to be
# glibc/Linux. No target code is executed by the configuration probe above.
add_compile_definitions(_GNU_SOURCE=1)
