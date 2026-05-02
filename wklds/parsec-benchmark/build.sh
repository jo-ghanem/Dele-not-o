#!/usr/bin/env bash
# Define target benchmarks in Ciro's repo structure
TARGET_APPS="blackscholes bodytrack fluidanimate freqmine raytrace vips x264"
TARGET_KERNELS="canneal dedup ferret"

# Function to compile
build_parsec() {
    for bmk in $1; do
        # Determine if it's in apps or kernels
        if [ -d "pkgs/apps/$bmk/src" ]; then
            DIR="pkgs/apps/$bmk/src"
        else
            DIR="pkgs/kernels/$bmk/src"
        fi

        echo "----------------------------------------"
        echo "Building $bmk in $DIR"
        echo "----------------------------------------"
        
        # Clean and Build
        make -C $DIR clean
        make -C $DIR CC=aarch64-unknown-linux-gnu-gcc \
                     CXX=aarch64-unknown-linux-gnu-g++ \
                     LDFLAGS="-static" -j$(nproc)
    done
}

# Run the build
build_parsec "$TARGET_APPS $TARGET_KERNELS"
