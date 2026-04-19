#!/bin/bash
# run_splash4_comparison.sh — Run SPLASH-4 benchmarks under gem5 with policy=0 and policy=1
# Compares DelegateAMO counters between centralized and delegated AMO execution.
# Usage: ./ecn/env_wrap.sh ./ecn/run_splash4_comparison.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTDIR="${JOB_DIR:-$REPO_ROOT}/m5out"
mkdir -p "$OUTDIR"

GEM5="$REPO_ROOT/build/ARM/gem5.opt"
CONFIG="$REPO_ROOT/configs/example/chi_benchmark_se.py"

# --- Step 0: Build gem5 if needed ---
if [ ! -x "$GEM5" ]; then
    echo "Building gem5 (ARM/CHI)..."
    cd "$REPO_ROOT"
    scons-3 USE_HDF5=0 -j 16 build/ARM/gem5.opt 2>&1 | tail -20
    echo ""
fi

# --- Step 1: Build SPLASH-4 benchmarks ---
echo "=== Step 1: Build SPLASH-4 benchmarks ==="
SPLASH4_BASE="$HOME/ece666/benchmarks/Splash-4"
SPLASH4_SRC="$SPLASH4_BASE/Splash-4"
SPLASH4_BIN="$OUTDIR/splash4_bins"
mkdir -p "$SPLASH4_BIN"

TOOLCHAIN_DIR="$HOME/bin_gem5/arm-gnu-toolchain-13.3.rel1-x86_64-aarch64-none-linux-gnu"
CC_ARM="$TOOLCHAIN_DIR/bin/aarch64-none-linux-gnu-gcc"
OBJDUMP="$TOOLCHAIN_DIR/bin/aarch64-none-linux-gnu-objdump"

export CC="$CC_ARM"
export CFLAGS="-O2 -static -march=armv8.1-a -pthread -D_GNU_SOURCE"
export LDFLAGS="-lm -lpthread -static"
export M4="m4"

build_splash4() {
    local name="$1"
    local dir="$2"
    [ ! -d "$dir" ] && return

    echo -n "  Building $name ... "
    if (cd "$dir" && make clean 2>/dev/null; \
        make BASEDIR="$SPLASH4_BASE" CC="$CC" CFLAGS="$CFLAGS" LDFLAGS="$LDFLAGS" M4="$M4" \
    ) > "$OUTDIR/build_${name}.log" 2>&1; then
        local target_bin=""
        for candidate in "$dir/FFT" "$dir/RADIX" "$dir/LU" "$dir/CHOLESKY" \
                         "$dir/BARNES" "$dir/FMM" "$dir/OCEAN" \
                         "$dir/WATER-NSQUARED" "$dir/WATER-SPATIAL"; do
            [ -x "$candidate" ] && target_bin="$candidate" && break
        done
        [ -z "$target_bin" ] && target_bin=$(find "$dir" -maxdepth 1 -type f -executable -newer "$dir/Makefile" 2>/dev/null | head -1)
        if [ -n "$target_bin" ]; then
            cp "$target_bin" "$SPLASH4_BIN/$name"
            echo "OK"
        else
            echo "BUILT but binary not found"
        fi
    else
        echo "FAILED"
    fi
}

build_splash4 "fft"       "$SPLASH4_SRC/fft"
build_splash4 "radix"     "$SPLASH4_SRC/radix"
build_splash4 "lu_cb"     "$SPLASH4_SRC/lu-contiguous_blocks"
build_splash4 "barnes"    "$SPLASH4_SRC/barnes"
build_splash4 "fmm"       "$SPLASH4_SRC/fmm"
build_splash4 "ocean_cp"  "$SPLASH4_SRC/ocean-contiguous_partitions"
build_splash4 "water_nsq" "$SPLASH4_SRC/water-nsquared"
build_splash4 "water_sp"  "$SPLASH4_SRC/water-spatial"
echo ""

# --- Step 2: Define benchmark configurations ---
# Each entry: name binary_name args
# Use small inputs so gem5 SE mode finishes in reasonable time
BENCHMARKS=(
    "fft:fft:-p 2 -m 16"
    "radix:radix:-p 2 -n 1024 -r 512"
    "ocean_cp:ocean_cp:-p 2 -n 66"
)

# --- Step 2a: Build + add AMO micro-benchmark ---
AMO_SRC="$REPO_ROOT/ecn/test-bins/amo_delegate_test.c"
AMO_BIN="$SPLASH4_BIN/amo_test"
if [ -f "$AMO_SRC" ]; then
    TOOLCHAIN_DIR2="$HOME/bin_gem5/arm-gnu-toolchain-13.3.rel1-x86_64-aarch64-none-linux-gnu"
    "$TOOLCHAIN_DIR2/bin/aarch64-none-linux-gnu-gcc" -O2 -static -march=armv8.1-a -pthread \
        -o "$AMO_BIN" "$AMO_SRC" -lm 2>/dev/null && echo "Built AMO micro-benchmark"
    BENCHMARKS=("amo_test:amo_test:-t 2 -n 100" "${BENCHMARKS[@]}")
fi

CORES=2
RESULTS_FILE="$OUTDIR/comparison_results.txt"
echo "=== SPLASH-4 Delegate AMO Comparison ===" > "$RESULTS_FILE"
echo "Date: $(date)" >> "$RESULTS_FILE"
echo "Cores: $CORES" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"

# --- Step 3: Run each benchmark under both policies ---
run_benchmark() {
    local name="$1"
    local binary="$2"
    local bench_args="$3"
    local policy="$4"
    local run_dir="$OUTDIR/${name}_policy${policy}"

    local bin_path="$SPLASH4_BIN/$binary"
    if [ ! -x "$bin_path" ]; then
        echo "  SKIP $name (binary not found)"
        return 1
    fi

    mkdir -p "$run_dir"

    echo -n "  Running $name (policy=$policy) ... "

    # Build command as array to preserve quoting
    local -a cmd=(timeout 600 "$GEM5"
        --debug-flags=DelegateAMO
        --debug-file=delegate_trace.txt
        "--outdir=$run_dir"
        "$CONFIG"
        --binary "$bin_path"
        --num-cores "$CORES"
        --hn-amo-policy "$policy"
        --cpu-type timing
    )
    [ -n "$bench_args" ] && cmd+=(--args "$bench_args")

    if "${cmd[@]}" > "$run_dir/gem5_stdout.txt" 2>&1; then
        echo "DONE"
        return 0
    else
        local exit_code=$?
        if [ $exit_code -eq 124 ]; then
            echo "TIMEOUT (600s)"
        else
            echo "FAILED (exit=$exit_code)"
            tail -5 "$run_dir/gem5_stdout.txt" 2>/dev/null || true
        fi
        return 1
    fi
}

extract_counters() {
    local name="$1"
    local policy="$2"
    local run_dir="$OUTDIR/${name}_policy${policy}"
    local trace="$run_dir/delegate_trace.txt"

    if [ ! -f "$trace" ]; then
        echo "  [policy=$policy] No trace file"
        return
    fi

    # grep -c outputs "0" AND exits 1 when no match; || echo 0 would
    # append a second "0" producing "0\n0" which printf %d rejects.
    # Use || true instead — grep -c always prints a count even for 0 matches.
    local a1=$(grep -c 'A1:' "$trace" 2>/dev/null || true)
    local a2=$(grep -c 'A2:' "$trace" 2>/dev/null || true)
    local a3=$(grep -c 'A3:' "$trace" 2>/dev/null || true)
    local b1=$(grep -c 'B1:' "$trace" 2>/dev/null || true)
    local b2=$(grep -c 'B2:' "$trace" 2>/dev/null || true)
    local b6=$(grep -c 'B6:' "$trace" 2>/dev/null || true)
    local c1=$(grep -c 'C1:' "$trace" 2>/dev/null || true)
    local c2=$(grep -c 'C2:' "$trace" 2>/dev/null || true)
    local e1=$(grep -c 'E1:' "$trace" 2>/dev/null || true)
    local accept=$(grep -c 'decision=accept' "$trace" 2>/dev/null || true)

    # Get tick from stats.txt
    local ticks=""
    if [ -f "$run_dir/stats.txt" ]; then
        ticks=$(grep 'simTicks' "$run_dir/stats.txt" 2>/dev/null | head -1 | awk '{print $2}')
    fi
    # Fallback: parse gem5 stdout for exit tick
    if [ -z "$ticks" ]; then
        ticks=$(grep 'Exiting @ tick' "$run_dir/gem5_stdout.txt" 2>/dev/null | tail -1 | grep -o '[0-9]*' || true)
        [ -z "$ticks" ] && ticks="?"
    fi

    printf "  [policy=%d] A1=%d A2=%d A3=%d | B1=%d B2=%d B6=%d | C1=%d C2=%d | E1=%d | ticks=%s\n" \
        "$policy" "$a1" "$a2" "$a3" "$b1" "$b2" "$b6" "$c1" "$c2" "$e1" "$ticks"

    # Append to results file
    printf "%s,policy=%d,A1=%d,A2=%d,A3=%d,B1=%d,B2=%d,B6=%d,C1=%d,C2=%d,E1=%d,accept=%d,ticks=%s\n" \
        "$name" "$policy" "$a1" "$a2" "$a3" "$b1" "$b2" "$b6" "$c1" "$c2" "$e1" "$accept" "$ticks" \
        >> "$RESULTS_FILE"
}

echo "=== Step 2: Run benchmarks ==="
for entry in "${BENCHMARKS[@]}"; do
    IFS=':' read -r name binary bench_args <<< "$entry"

    echo ""
    echo "--- $name ---"

    # Run baseline (policy=0)
    if run_benchmark "$name" "$binary" "$bench_args" 0; then
        extract_counters "$name" 0
    fi

    # Run delegate (policy=1)
    if run_benchmark "$name" "$binary" "$bench_args" 1; then
        extract_counters "$name" 1
    fi
done

# --- Step 4: Summary ---
echo ""
echo "=========================================="
echo "=== COMPARISON SUMMARY ==="
echo "=========================================="
echo ""

cat "$RESULTS_FILE"

echo ""
echo "=== Key metrics ==="
echo "C2 > 0 with policy=1 confirms delegate AMO execution at owner"
echo "B6 > 0 indicates retries (owner rejected SnpAMO)"
echo "Tick comparison shows performance impact"
echo ""
echo "Full results: $RESULTS_FILE"
echo "Per-benchmark traces: $OUTDIR/<name>_policy<N>/delegate_trace.txt"
