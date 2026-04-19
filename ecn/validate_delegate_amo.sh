#!/bin/bash
# validate_delegate_amo.sh — Build AMO test binary + run under gem5 with DelegateAMO traces
# Usage: ./ecn/env_wrap.sh ./ecn/validate_delegate_amo.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTDIR="${JOB_DIR:-$REPO_ROOT}/m5out"
mkdir -p "$OUTDIR"

TOOLCHAIN_DIR="$HOME/bin_gem5/arm-gnu-toolchain-13.3.rel1-x86_64-aarch64-none-linux-gnu"
CC="$TOOLCHAIN_DIR/bin/aarch64-none-linux-gnu-gcc"
OBJDUMP="$TOOLCHAIN_DIR/bin/aarch64-none-linux-gnu-objdump"
GEM5="$REPO_ROOT/build/ARM/gem5.opt"

echo "=== Delegate AMO Validation ==="
echo "Repo: $REPO_ROOT"
echo "Output: $OUTDIR"
echo "CC: $CC"
echo ""

# --- Step 0: Ensure gem5 is built ---
if [ ! -x "$GEM5" ]; then
    echo "Building gem5 (ARM/CHI)..."
    cd "$REPO_ROOT"
    scons-3 USE_HDF5=0 -j 16 build/ARM/gem5.opt 2>&1 | tail -20
    echo ""
fi

# --- Step 1: Build AMO micro-benchmark ---
echo "=== Step 1: Build AMO micro-benchmark ==="
AMO_SRC="$REPO_ROOT/ecn/test-bins/amo_delegate_test.c"
AMO_BIN="$OUTDIR/amo_delegate_test"

if [ ! -f "$AMO_SRC" ]; then
    echo "ERROR: AMO test source not found at $AMO_SRC"
    exit 1
fi

$CC -O2 -static -march=armv8.1-a -pthread -o "$AMO_BIN" "$AMO_SRC" -lm
LSE_COUNT=$($OBJDUMP -d "$AMO_BIN" 2>/dev/null | grep -ciE '\bldadd\b|\bcas\b|\bswp\b|\bstadd\b|\bldset\b|\bldclr\b' || echo 0)
echo "Built: $AMO_BIN (LSE atomics: $LSE_COUNT)"
echo ""

# --- Step 2: Build SPLASH-4 benchmarks via make ---
echo "=== Step 2: Build SPLASH-4 benchmarks ==="
SPLASH4_BASE="$HOME/ece666/benchmarks/Splash-4"
SPLASH4_SRC="$SPLASH4_BASE/Splash-4"
SPLASH4_BIN="$OUTDIR/splash4_bins"
mkdir -p "$SPLASH4_BIN"

# Override Makefile.config variables for cross-compilation
export CC="$CC"
export CFLAGS="-O2 -static -march=armv8.1-a -pthread -D_GNU_SOURCE"
export LDFLAGS="-lm -lpthread -static"
export M4="m4"

BUILT=0
FAILED=0

build_splash4() {
    local name="$1"
    local dir="$2"

    if [ ! -d "$dir" ]; then
        echo "  SKIP $name (dir not found)"
        return
    fi

    echo -n "  Building $name ... "
    # Clean first, then build with cross-compiler
    if (cd "$dir" && make clean 2>/dev/null; \
        make BASEDIR="$SPLASH4_BASE" \
             CC="$CC" \
             CFLAGS="$CFLAGS" \
             LDFLAGS="$LDFLAGS" \
             M4="$M4" \
        ) > "$OUTDIR/build_${name}.log" 2>&1; then

        # Find the built binary (target name varies per benchmark)
        local target_bin=""
        for candidate in "$dir/FFT" "$dir/RADIX" "$dir/LU" "$dir/CHOLESKY" \
                         "$dir/BARNES" "$dir/FMM" "$dir/OCEAN" \
                         "$dir/WATER-NSQUARED" "$dir/WATER-SPATIAL" \
                         "$dir/$(echo "$name" | tr '[:lower:]' '[:upper:]')"; do
            if [ -x "$candidate" ]; then
                target_bin="$candidate"
                break
            fi
        done

        # Also check for any new executable file
        if [ -z "$target_bin" ]; then
            target_bin=$(find "$dir" -maxdepth 1 -type f -executable -newer "$dir/Makefile" 2>/dev/null | head -1)
        fi

        if [ -n "$target_bin" ]; then
            cp "$target_bin" "$SPLASH4_BIN/$name"
            local lse=$($OBJDUMP -d "$SPLASH4_BIN/$name" 2>/dev/null | grep -ciE '\bldadd\b|\bcas\b|\bswp\b|\bstadd\b|\bldset\b|\bldclr\b' || echo 0)
            echo "OK (LSE: $lse)"
            BUILT=$((BUILT+1))
        else
            echo "BUILT but binary not found (check $OUTDIR/build_${name}.log)"
            FAILED=$((FAILED+1))
        fi
    else
        echo "FAILED (see $OUTDIR/build_${name}.log)"
        tail -5 "$OUTDIR/build_${name}.log"
        FAILED=$((FAILED+1))
    fi
}

build_splash4 "fft"         "$SPLASH4_SRC/fft"
build_splash4 "radix"       "$SPLASH4_SRC/radix"
build_splash4 "lu_cb"       "$SPLASH4_SRC/lu-contiguous_blocks"
build_splash4 "lu_ncb"      "$SPLASH4_SRC/lu-non_contiguous_blocks"
build_splash4 "cholesky"    "$SPLASH4_SRC/cholesky"
build_splash4 "barnes"      "$SPLASH4_SRC/barnes"
build_splash4 "fmm"         "$SPLASH4_SRC/fmm"
build_splash4 "ocean_cp"    "$SPLASH4_SRC/ocean-contiguous_partitions"
build_splash4 "ocean_ncp"   "$SPLASH4_SRC/ocean-non_contiguous_partitions"
build_splash4 "water_nsq"   "$SPLASH4_SRC/water-nsquared"
build_splash4 "water_sp"    "$SPLASH4_SRC/water-spatial"

echo ""
echo "SPLASH-4 results: $BUILT built, $FAILED failed"
echo ""

# --- Step 3: Run AMO micro-benchmark under gem5 ---
echo "=== Step 3: Run AMO micro-benchmark (policy=1, Pinned Owner) ==="
AMO_OUT="$OUTDIR/amo_test_policy1"
mkdir -p "$AMO_OUT"

$GEM5 \
    --debug-flags=DelegateAMO \
    --debug-file=delegate_trace.txt \
    --outdir="$AMO_OUT" \
    "$REPO_ROOT/configs/example/chi_benchmark_se.py" \
    --binary "$AMO_BIN" \
    --args "-t 2 -n 100" \
    --num-cores 2 \
    --hn-amo-policy 1 \
    --cpu-type timing \
    2>&1 | tee "$AMO_OUT/gem5_stdout.txt"

echo ""
echo "=== Step 4: Analyze DelegateAMO counters ==="

TRACE="$AMO_OUT/delegate_trace.txt"
if [ -f "$TRACE" ]; then
    echo "Trace file: $(wc -l < "$TRACE") lines"
    echo ""
    echo "--- Counter summary ---"
    echo "A1 (delegate decisions):  $(grep -c 'A1:' "$TRACE" 2>/dev/null || echo 0)"
    echo "A2 (centralize decisions): $(grep -c 'A2:' "$TRACE" 2>/dev/null || echo 0)"
    echo "A3 (no-owner decisions):  $(grep -c 'A3:' "$TRACE" 2>/dev/null || echo 0)"
    echo "B1 (SnpAMO sent):        $(grep -c 'B1:' "$TRACE" 2>/dev/null || echo 0)"
    echo "B2 (SnpAMO received):    $(grep -c 'B2:' "$TRACE" 2>/dev/null || echo 0)"
    echo "B6 (retry sent):         $(grep -c 'B6:' "$TRACE" 2>/dev/null || echo 0)"
    echo "B7 (retry received):     $(grep -c 'B7:' "$TRACE" 2>/dev/null || echo 0)"
    echo "C1 (HN atomicPartial):   $(grep -c 'C1:' "$TRACE" 2>/dev/null || echo 0)"
    echo "C2 (owner atomicPartial): $(grep -c 'C2:' "$TRACE" 2>/dev/null || echo 0)"
    echo "E1 (recentralized):      $(grep -c 'E1:' "$TRACE" 2>/dev/null || echo 0)"
    echo ""

    # Check for B2 accept vs reject breakdown
    ACCEPT=$(grep -c 'decision=accept' "$TRACE" 2>/dev/null || echo 0)
    REJECT=$(grep -c 'decision=reject' "$TRACE" 2>/dev/null || echo 0)
    echo "B2 breakdown: accept=$ACCEPT, reject=$REJECT"
    echo ""

    # Sample of delegate traces
    echo "--- Sample delegate traces (first 30 lines) ---"
    head -30 "$TRACE"
    echo ""
    echo "--- Sample reject reasons ---"
    grep 'decision=reject' "$TRACE" 2>/dev/null | head -10 || echo "(none)"
else
    echo "WARNING: No trace file at $TRACE"
fi

echo ""
echo "=== Step 5: Run baseline comparison (policy=0, All-Central) ==="
AMO_OUT_BASE="$OUTDIR/amo_test_policy0"
mkdir -p "$AMO_OUT_BASE"

$GEM5 \
    --debug-flags=DelegateAMO \
    --debug-file=delegate_trace.txt \
    --outdir="$AMO_OUT_BASE" \
    "$REPO_ROOT/configs/example/chi_benchmark_se.py" \
    --binary "$AMO_BIN" \
    --args "-t 2 -n 100" \
    --num-cores 2 \
    --hn-amo-policy 0 \
    --cpu-type timing \
    2>&1 | tee "$AMO_OUT_BASE/gem5_stdout.txt"

TRACE_BASE="$AMO_OUT_BASE/delegate_trace.txt"
if [ -f "$TRACE_BASE" ]; then
    echo ""
    echo "--- Baseline counter summary (policy=0) ---"
    echo "A1 (delegate): $(grep -c 'A1:' "$TRACE_BASE" 2>/dev/null || echo 0)"
    echo "A2 (centralize): $(grep -c 'A2:' "$TRACE_BASE" 2>/dev/null || echo 0)"
    echo "C1 (HN execute): $(grep -c 'C1:' "$TRACE_BASE" 2>/dev/null || echo 0)"
    echo "C2 (owner execute): $(grep -c 'C2:' "$TRACE_BASE" 2>/dev/null || echo 0)"
fi

echo ""
echo "=== Validation complete ==="
echo "Output directory: $OUTDIR"
ls -la "$OUTDIR/" 2>/dev/null || true
