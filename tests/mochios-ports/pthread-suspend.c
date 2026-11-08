/* Native prerequisite for WTF's signal/sem/sigsuspend GC handshake.
 * This tests the real SDK in mochiOS, not a host pthread approximation. */
#define _GNU_SOURCE 1
#include <errno.h>
#include <pthread.h>
#include <semaphore.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <ucontext.h>

_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "signal handshake needs lock-free atomics");
static sem_t ready, suspended, resumed;
static atomic_int phase, stop_worker, failure, resume_signal_seen;
static uintptr_t stack_low, stack_high, saved_sp, callback_sp;

static int wait_for(sem_t *sem)
{
    int rc;
    do { rc = sem_wait(sem); } while (rc == -1 && errno == EINTR);
    return rc;
}

static void suspend_handler(int signal, siginfo_t *info, void *raw)
{
    (void)signal;
    (void)info;
    if (atomic_load_explicit(&phase, memory_order_acquire)) {
        atomic_fetch_add_explicit(&resume_signal_seen, 1, memory_order_release);
        return;
    }
    char marker;
    callback_sp = (uintptr_t)&marker;
    ucontext_t *context = raw;
    saved_sp = context ? context->uc_mcontext.gregs[REG_RSP] : 0;
    if (saved_sp < stack_low || saved_sp >= stack_high)
        atomic_store(&failure, 10);
    if (sem_post(&suspended))
        atomic_store(&failure, 11);
    sigset_t mask;
    sigfillset(&mask);
    sigdelset(&mask, SIGUSR2);
    if (sigsuspend(&mask) != -1 || errno != EINTR)
        atomic_store(&failure, 12);
    if (atomic_load_explicit(&resume_signal_seen, memory_order_acquire) != 1)
        atomic_store(&failure, 14);
    if (sem_post(&resumed))
        atomic_store(&failure, 13);
}

static void *worker(void *unused)
{
    (void)unused;
    pthread_attr_t attr;
    void *base = NULL;
    size_t length = 0;
    int error = pthread_getattr_np(pthread_self(), &attr);
    if (!error) {
        error = pthread_attr_getstack(&attr, &base, &length);
        pthread_attr_destroy(&attr);
    }
    if (error || !base || !length)
        atomic_store(&failure, 20);
    stack_low = (uintptr_t)base;
    stack_high = stack_low + length;
    if (sem_post(&ready))
        atomic_store(&failure, 21);
    /* No RPC/sleep/poll: delivery must interrupt actual running user code. */
    while (!atomic_load_explicit(&stop_worker, memory_order_acquire))
        atomic_signal_fence(memory_order_seq_cst);
    return NULL;
}

int native_gc_suspend_probe(void)
{
    if (sem_init(&ready, 0, 0) || sem_init(&suspended, 0, 0) || sem_init(&resumed, 0, 0))
        return 1;
    puts("NATIVE GC installing handler");
    struct sigaction action = {0};
    action.sa_sigaction = suspend_handler;
    action.sa_flags = SA_SIGINFO | SA_RESTART;
    sigfillset(&action.sa_mask);
    if (sigaction(SIGUSR2, &action, NULL))
        return 2;
    sigset_t signal;
    sigemptyset(&signal);
    sigaddset(&signal, SIGUSR2);
    if (pthread_sigmask(SIG_UNBLOCK, &signal, NULL))
        return 3;
    pthread_attr_t attr;
    if (pthread_attr_init(&attr) || pthread_attr_setstacksize(&attr, 128 * 1024) ||
        pthread_attr_setguardsize(&attr, 8192))
        return 4;
    puts("NATIVE GC creating worker");
    pthread_t thread;
    int error = pthread_create(&thread, &attr, worker, NULL);
    pthread_attr_destroy(&attr);
    if (error)
        return 5;
    puts("NATIVE GC waiting for worker");
    if (wait_for(&ready))
        return 5;
    puts("NATIVE GC worker ready");
    if (pthread_kill(thread, SIGUSR2))
        return 6;
    puts("NATIVE GC suspend sent");
    if (wait_for(&suspended))
        return 6;
    puts("NATIVE GC suspended");
    atomic_store_explicit(&phase, 1, memory_order_release);
    if (pthread_kill(thread, SIGUSR2))
        return 7;
    puts("NATIVE GC resume sent");
    if (wait_for(&resumed))
        return 7;
    puts("NATIVE GC resumed");
    atomic_store_explicit(&stop_worker, 1, memory_order_release);
    if (pthread_join(thread, NULL))
        return 8;
    error = atomic_load(&failure);
    if (error)
        return error;
    /* The SDK enters C handlers on the interrupted application's stack,
     * below the x86-64 red zone. Its internal upcall frames are private.
     * This preserves upstream WTF's currentStackPointer/alt-stack check. */
    if (callback_sp < stack_low || callback_sp >= stack_high ||
        callback_sp > saved_sp || saved_sp - callback_sp < 128)
        return 22;
    printf("NATIVE GC SUSPEND PASS saved_sp=%lx callback_sp=%lx stack=%lx..%lx\n",
        (unsigned long)saved_sp, (unsigned long)callback_sp,
        (unsigned long)stack_low, (unsigned long)stack_high);
    sem_destroy(&ready);
    sem_destroy(&suspended);
    sem_destroy(&resumed);
    return 0;
}
