#!/bin/bash
# Build SPLASH-4 benchmarks for ARM aarch64 (statically linked, for gem5 SE mode)
set -euo pipefail

SPLASH4_BASE="${1:-$HOME/ece666/benchmarks/Splash-4/Splash-4}"
OUTDIR="${2:-$HOME/ece666/benchmarks/splash4_arm_bin}"
CC="aarch64-linux-gnu-gcc"

if ! command -v "$CC" &>/dev/null; then
    # Try ECN paths
    for p in /package/gcc/8.3.0/bin /usr/bin; do
        if [ -x "$p/aarch64-linux-gnu-gcc" ]; then
            CC="$p/aarch64-linux-gnu-gcc"
            break
        fi
    done
fi

if ! command -v "$CC" &>/dev/null; then
    echo "ERROR: aarch64-linux-gnu-gcc not found. Install with:"
    echo "  sudo apt install gcc-aarch64-linux-gnu"
    exit 1
fi

mkdir -p "$OUTDIR"
echo "=== Building SPLASH-4 for ARM aarch64 ==="
echo "Source: $SPLASH4_BASE"
echo "Output: $OUTDIR"
echo "CC: $CC"
echo ""

BUILT=0
FAILED=0

build_bench() {
    local name="$1"
    local srcdir="$2"
    local srcs="$3"
    local extra_flags="${4:-}"

    echo -n "Building $name ... "
    if $CC -O2 -static -pthread $extra_flags -o "$OUTDIR/$name" $srcs -lm 2>/tmp/build_${name}.log; then
        echo "OK ($(file "$OUTDIR/$name" | grep -o 'ARM aarch64' || echo 'built'))"
        BUILT=$((BUILT+1))
    else
        echo "FAILED (see /tmp/build_${name}.log)"
        FAILED=$((FAILED+1))
    fi
}

# Kernel benchmarks
if [ -d "$SPLASH4_BASE/fft" ]; then
    build_bench "FFT" "$SPLASH4_BASE/fft" "$SPLASH4_BASE/fft/*.c"
fi

if [ -d "$SPLASH4_BASE/radix" ]; then
    build_bench "RADIX" "$SPLASH4_BASE/radix" "$SPLASH4_BASE/radix/*.c"
fi

if [ -d "$SPLASH4_BASE/lu-contiguous_blocks" ]; then
    build_bench "LU_CB" "$SPLASH4_BASE/lu-contiguous_blocks" "$SPLASH4_BASE/lu-contiguous_blocks/*.c"
fi

if [ -d "$SPLASH4_BASE/lu-non_contiguous_blocks" ]; then
    build_bench "LU_NCB" "$SPLASH4_BASE/lu-non_contiguous_blocks" "$SPLASH4_BASE/lu-non_contiguous_blocks/*.c"
fi

if [ -d "$SPLASH4_BASE/cholesky" ]; then
    build_bench "CHOLESKY" "$SPLASH4_BASE/cholesky" "$SPLASH4_BASE/cholesky/*.c"
fi

# Application benchmarks
if [ -d "$SPLASH4_BASE/barnes" ]; then
    build_bench "BARNES" "$SPLASH4_BASE/barnes" "$SPLASH4_BASE/barnes/*.c"
fi

if [ -d "$SPLASH4_BASE/fmm" ]; then
    build_bench "FMM" "$SPLASH4_BASE/fmm" "$SPLASH4_BASE/fmm/*.c"
fi

if [ -d "$SPLASH4_BASE/ocean-contiguous_partitions" ]; then
    build_bench "OCEAN_CP" "$SPLASH4_BASE/ocean-contiguous_partitions" "$SPLASH4_BASE/ocean-contiguous_partitions/*.c"
fi

if [ -d "$SPLASH4_BASE/ocean-non_contiguous_partitions" ]; then
    build_bench "OCEAN_NCP" "$SPLASH4_BASE/ocean-non_contiguous_partitions" "$SPLASH4_BASE/ocean-non_contiguous_partitions/*.c"
fi

if [ -d "$SPLASH4_BASE/water-nsquared" ]; then
    build_bench "WATER_NSQ" "$SPLASH4_BASE/water-nsquared" "$SPLASH4_BASE/water-nsquared/*.c"
fi

if [ -d "$SPLASH4_BASE/water-spatial" ]; then
    build_bench "WATER_SP" "$SPLASH4_BASE/water-spatial" "$SPLASH4_BASE/water-spatial/*.c"
fi

echo ""
echo "=== Results: $BUILT built, $FAILED failed ==="
echo "Binaries in: $OUTDIR"
ls -la "$OUTDIR/" 2>/dev/null || true
