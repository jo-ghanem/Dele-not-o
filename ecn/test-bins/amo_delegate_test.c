/*
 * amo_delegate_test.c — Micro-benchmark for delegated AMO validation
 *
 * Exercises ARM LSE atomic instructions (LDADD, CAS, SWP) which map to
 * AtomicReturn/AtomicNoReturn in gem5's CHI protocol. When compiled with
 * -march=armv8.1-a, GCC emits actual LSE instructions instead of LL/SC loops.
 *
 * With policy_type=1 (far AMO) and hn_amo_policy=1 (Pinned Owner), these
 * atomics should exercise the delegated AMO path: SnpAMO → owner executes
 * RMW → SnpResp_UD_AMODone.
 *
 * Usage (in gem5 SE mode):
 *   build/ARM/gem5.opt configs/example/chi_benchmark_se.py \
 *       --binary tests/test-progs/amo_delegate_test --args "-t 4 -n 10000" \
 *       --num-cores 4 --hn-amo-policy 1
 *
 * Compile (cross-compile for ARM with LSE):
 *   aarch64-linux-gnu-gcc -O2 -static -march=armv8.1-a -pthread \
 *       -o amo_delegate_test amo_delegate_test.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>

/* Shared counters — all threads hammer these */
static _Atomic int64_t g_fetch_add_counter = 0;
static _Atomic int64_t g_cas_counter = 0;
static _Atomic int64_t g_swap_counter = 0;

/* Per-thread configuration */
typedef struct {
    int thread_id;
    int num_iters;
    int64_t local_sum;  /* for verification */
} thread_arg_t;

/*
 * fetch_add_worker: Each thread does atomic fetch-and-add on a shared counter.
 * On ARM with LSE, this compiles to LDADD instruction.
 */
static void *fetch_add_worker(void *arg) {
    thread_arg_t *ta = (thread_arg_t *)arg;
    int64_t sum = 0;

    for (int i = 0; i < ta->num_iters; i++) {
        /* __atomic_fetch_add → LDADD (LSE) or LDXR/STXR loop (no LSE) */
        int64_t old = atomic_fetch_add_explicit(&g_fetch_add_counter, 1,
                                                 memory_order_relaxed);
        sum += old;
    }

    ta->local_sum = sum;
    return NULL;
}

/*
 * cas_worker: Each thread does compare-and-swap in a loop (mutex-like pattern).
 * On ARM with LSE, this compiles to CAS instruction.
 */
static void *cas_worker(void *arg) {
    thread_arg_t *ta = (thread_arg_t *)arg;
    int successes = 0;

    for (int i = 0; i < ta->num_iters; i++) {
        int64_t expected = atomic_load_explicit(&g_cas_counter,
                                                 memory_order_relaxed);
        int64_t desired;
        do {
            desired = expected + 1;
            /* atomic_compare_exchange_weak → CAS (LSE) or LDXR/STXR */
        } while (!atomic_compare_exchange_weak_explicit(
                    &g_cas_counter, &expected, desired,
                    memory_order_relaxed, memory_order_relaxed));
        successes++;
    }

    ta->local_sum = successes;
    return NULL;
}

/*
 * swap_worker: Each thread does atomic exchange (swap).
 * On ARM with LSE, this compiles to SWP instruction.
 */
static void *swap_worker(void *arg) {
    thread_arg_t *ta = (thread_arg_t *)arg;
    int64_t last_val = 0;

    for (int i = 0; i < ta->num_iters; i++) {
        /* atomic_exchange → SWP (LSE) or LDXR/STXR */
        last_val = atomic_exchange_explicit(&g_swap_counter,
                                            (int64_t)(ta->thread_id * 1000 + i),
                                            memory_order_relaxed);
    }

    ta->local_sum = last_val;
    return NULL;
}

static void usage(const char *prog) {
    fprintf(stderr, "Usage: %s [-t threads] [-n iterations]\n", prog);
    fprintf(stderr, "  -t  Number of threads (default 4)\n");
    fprintf(stderr, "  -n  Iterations per thread (default 1000)\n");
    exit(1);
}

int main(int argc, char **argv) {
    int num_threads = 4;
    int num_iters = 1000;

    /* Parse arguments */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-t") == 0 && i + 1 < argc)
            num_threads = atoi(argv[++i]);
        else if (strcmp(argv[i], "-n") == 0 && i + 1 < argc)
            num_iters = atoi(argv[++i]);
        else
            usage(argv[0]);
    }

    printf("AMO Delegate Test: %d threads, %d iterations each\n",
           num_threads, num_iters);

    pthread_t *threads = malloc(num_threads * sizeof(pthread_t));
    thread_arg_t *args = malloc(num_threads * sizeof(thread_arg_t));

    /* ===== Test 1: Fetch-and-Add (LDADD) ===== */
    printf("\n--- Test 1: Fetch-and-Add (LDADD on LSE) ---\n");
    g_fetch_add_counter = 0;
    for (int i = 0; i < num_threads; i++) {
        args[i].thread_id = i;
        args[i].num_iters = num_iters;
        args[i].local_sum = 0;
        pthread_create(&threads[i], NULL, fetch_add_worker, &args[i]);
    }
    for (int i = 0; i < num_threads; i++)
        pthread_join(threads[i], NULL);

    int64_t expected_faa = (int64_t)num_threads * num_iters;
    printf("  Final counter: %lld (expected %lld) — %s\n",
           (long long)g_fetch_add_counter, (long long)expected_faa,
           g_fetch_add_counter == expected_faa ? "PASS" : "FAIL");

    /* ===== Test 2: Compare-and-Swap (CAS) ===== */
    printf("\n--- Test 2: Compare-and-Swap (CAS on LSE) ---\n");
    g_cas_counter = 0;
    for (int i = 0; i < num_threads; i++) {
        args[i].thread_id = i;
        args[i].num_iters = num_iters;
        args[i].local_sum = 0;
        pthread_create(&threads[i], NULL, cas_worker, &args[i]);
    }
    for (int i = 0; i < num_threads; i++)
        pthread_join(threads[i], NULL);

    int64_t expected_cas = (int64_t)num_threads * num_iters;
    printf("  Final counter: %lld (expected %lld) — %s\n",
           (long long)g_cas_counter, (long long)expected_cas,
           g_cas_counter == expected_cas ? "PASS" : "FAIL");

    /* ===== Test 3: Swap (SWP) ===== */
    printf("\n--- Test 3: Swap (SWP on LSE) ---\n");
    g_swap_counter = 0;
    for (int i = 0; i < num_threads; i++) {
        args[i].thread_id = i;
        args[i].num_iters = num_iters;
        args[i].local_sum = 0;
        pthread_create(&threads[i], NULL, swap_worker, &args[i]);
    }
    for (int i = 0; i < num_threads; i++)
        pthread_join(threads[i], NULL);

    printf("  Swap counter final value: %lld (non-deterministic, just checking no crash)\n",
           (long long)g_swap_counter);

    /* ===== Summary ===== */
    printf("\n=== Summary ===\n");
    printf("All tests completed. Check gem5 DelegateAMO traces to confirm:\n");
    printf("  - A1:delegate counters > 0 (policy decision fired)\n");
    printf("  - B1/B2: SnpAMO sent/received\n");
    printf("  - C2: owner_atomic_partial executed\n");
    printf("  - B3/B4: SnpResp_UD_AMODone sent/received\n");

    free(threads);
    free(args);
    return 0;
}
