#!/usr/bin/env python3
import subprocess
import argparse
import os
import sys

# --- Base Directory Definitions ---
# Using explicit shared path so other users can access your workloads
SHARED_HOME = "/home/toni" 
SPLASH_BASE = f"{SHARED_HOME}/amo/wklds/Splash-4/Splash-4"
PARSEC_BASE = f"{SHARED_HOME}/amo/wklds/parsec-benchmark"
PARSEC_BIN_DIR = f"{SHARED_HOME}/amo/wklds/binaries/parsec"

# Gem5 resources remain local to the user's current working directory
GEM5_ROOT = os.getcwd()
GEM5_BIN = os.path.join(GEM5_ROOT, "build/ARM/gem5.opt")
GEM5_CONFIG = os.path.join(GEM5_ROOT, "configs/deprecated/example/se.py")
OUTPUT_ROOT = os.path.join(GEM5_ROOT, "m5out")

# --- Benchmark Definitions ---
BMK_CONFIG = {
    # --- Splash-4 Benchmarks ---
    "barnes": {
        "root": SPLASH_BASE,
        "cmd": "barnes/BARNES",
        "mode": "input",
        "args": "barnes/inputs/n16384-p{N}"
    },
    "fmm": {
        "root": SPLASH_BASE,
        "cmd": "fmm/FMM",
        "mode": "input",
        "args": "fmm/inputs/input.{N}.16384"
    },
    "fft": {
        "root": SPLASH_BASE,
        "cmd": "fft/FFT",
        "mode": "options",
        "args": "-p{N} -m16"
    },
    "cholesky": {
        "root": SPLASH_BASE,
        "cmd": "cholesky/CHOLESKY",
        "mode": "options",
        "args": "-p{N} -f {SPLASH_BASE}/cholesky/inputs/tk15.O"
    },
    "lu_cont": {
        "root": SPLASH_BASE,
        "cmd": "lu-contiguous_blocks/LU-CONT",
        "mode": "options",
        "args": "-p{N} -n512" 
    },
    "lu_noncont": {
        "root": SPLASH_BASE,
        "cmd": "lu-non_contiguous_blocks/LU-NOCONT",
        "mode": "options",
        "args": "-p{N} -n512"
    },
    "ocean_cont": {
        "root": SPLASH_BASE,
        "cmd": "ocean-contiguous_partitions/OCEAN-CONT",
        "mode": "options",
        "args": "-n258 -p{N}" 
    },
    "ocean_noncont": {
        "root": SPLASH_BASE,
        "cmd": "ocean-non_contiguous_partitions/OCEAN-NOCONT",
        "mode": "options",
        "args": "-n258 -p{N}"
    },
    "radiosity": {
        "root": SPLASH_BASE,
        "cmd": "radiosity/RADIOSITY",
        "mode": "options",
        "args": "-p {N} -batch -room"
    },
    "radix": {
        "root": SPLASH_BASE,
        "cmd": "radix/RADIX",
        "mode": "options",
        "args": "-p{N}"
    },
    "raytrace_sp": {
        "root": SPLASH_BASE,
        "cmd": "raytrace/RAYTRACE",
        "mode": "options",
        "args": "-p{N} {SPLASH_BASE}/raytrace/inputs/car.env"
    },
    "volrend": {
        "root": SPLASH_BASE,
        "cmd": "volrend/VOLREND",
        "mode": "options",
        "args": "{N} {SPLASH_BASE}/volrend/inputs/head"
    },
    "water_nsquared": {
        "root": SPLASH_BASE,
        "cmd": "water-nsquared/WATER-NSQUARED",
        "mode": "input",
        "args": "water-nsquared/inputs/n512-p{N}"
    },
    "water_spatial": {
        "root": SPLASH_BASE,
        "cmd": "water-spatial/WATER-SPATIAL",
        "mode": "input",
        "args": "water-spatial/inputs/n512-p{N}"
    },

    # --- PARSEC Benchmarks ---
    "blackscholes": {
        "root": PARSEC_BIN_DIR,
        "cmd": "blackscholes",
        "mode": "options",
        "args": "{N} {PARSEC_BASE}/pkgs/apps/blackscholes/inputs/in_4K.txt prices.txt"
    },
    "fluidanimate": {
        "root": PARSEC_BIN_DIR,
        "cmd": "fluidanimate",
        "mode": "options",
        "args": "{N} 5 {PARSEC_BASE}/pkgs/apps/fluidanimate/inputs/in_35K.fluid out.fluid"
    },
    "freqmine": {
        "root": PARSEC_BIN_DIR,
        "cmd": "freqmine",
        "mode": "options",
        "args": "{PARSEC_BASE}/pkgs/apps/freqmine/inputs/kosarak_250k.dat 11000"
    },
    "x264": {
        "root": PARSEC_BIN_DIR,
        "cmd": "x264",
        "mode": "options",
        "args": "--quiet --qp 20 --partitions b8x8,i4x4 --ref 5 --direct auto --b-pyramid --weightb --mixed-refs --no-fast-pskip --me umh --subme 7 --analyse b8x8,i4x4 --threads {N} -o eledream.264 {PARSEC_BASE}/pkgs/apps/x264/inputs/eledream_640x360_8.y4m"
    },
    "canneal": {
        "root": PARSEC_BIN_DIR,
        "cmd": "canneal",
        "mode": "options",
        "args": "{N} 10000 2000 {PARSEC_BASE}/pkgs/kernels/canneal/inputs/100000.nets 32"
    },
    "dedup": {
        "root": PARSEC_BIN_DIR,
        "cmd": "dedup",
        "mode": "options",
        "args": "-c -p -v -t {N} -i {PARSEC_BASE}/pkgs/kernels/dedup/inputs/media.dat -o output.dat.ddp"
    },
    "swaptions": {
        "root": PARSEC_BIN_DIR,
        "cmd": "swaptions",
        "mode": "options",
        "args": "-ns 16 -sm 5000 -nt {N}"
    },
    "streamcluster": {
        "root": PARSEC_BIN_DIR,
        "cmd": "streamcluster",
        "mode": "options",
        "args": "10 20 32 4096 4096 1000 none output.txt {N}"
    }
}

results_summary = []

def run_gem5(name, cpu_count):
    # Ensure name is lower case and stripped of category prefix if present
    clean_name = name.split('/')[-1].lower()
    
    if clean_name not in BMK_CONFIG:
        print(f"Error: Workload '{clean_name}' (derived from '{name}') not defined in config.")
        return

    cfg = BMK_CONFIG[clean_name]
    abs_cmd = os.path.join(cfg["root"], cfg["cmd"])
    out_dir = os.path.join(OUTPUT_ROOT, f"{clean_name}_p{cpu_count}")
    os.makedirs(out_dir, exist_ok=True)

    cmd = [
        GEM5_BIN, "-d", out_dir, GEM5_CONFIG,
        f"--cmd={abs_cmd}",
        f"--num-cpus={cpu_count}",
        "--cpu-type=AtomicSimpleCPU",
        "--mem-size=4GB", "--caches", "--l2cache",
        "--maxinsts=1000000"
    ]

    raw_args = cfg["args"].format(N=cpu_count, SPLASH_BASE=SPLASH_BASE, PARSEC_BASE=PARSEC_BASE)

    if cfg["mode"] == "input":
        abs_input = os.path.join(cfg["root"], raw_args)
        cmd.append(f"--input={abs_input}")
    else:
        cmd.append(f"--options={raw_args}")

    print(f"\n>>> Executing: {clean_name} with {cpu_count} CPU(s)")
    try:
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        output = process.stdout
        
        if "Exiting @ tick" in output:
            status = "SUCCESS"
        elif "fatal: Out of memory" in output:
            status = "FAILED (OOM)"
        elif "Failed to open file" in output or "No such file" in output:
            status = "FAILED (Path/File Error)"
        else:
            status = "FAILED (Check Output)"
            # Print last 15 lines of output for diagnosis
            print(f"\n--- [DEBUG] Last 15 lines of gem5 output for {clean_name} ---")
            print("\n".join(output.splitlines()[-15:]))
            
        results_summary.append({"name": clean_name, "status": status})
        print(f"Result for {clean_name}: {status}")
    except KeyboardInterrupt:
        sys.exit(1)

def print_report():
    print("\n" + "="*50)
    print(f"{'Workload':<18} | {'Status':<25}")
    print("-" * 50)
    for res in results_summary:
        print(f"{res['name']:<18} | {res['status']:<25}")
    print("=" * 50)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--list", type=str, help="splash or parsec")
    parser.add_argument("-p", "--prog", type=str, help="category/benchmark (e.g., parsec/x264)")
    parser.add_argument("-n", "--num", type=int, default=1)
    args = parser.parse_args()

    if args.prog:
        run_gem5(args.prog, args.num)
        print_report()
    elif args.list == "splash":
        for name, cfg in BMK_CONFIG.items():
            if cfg["root"] == SPLASH_BASE: run_gem5(name, args.num)
        print_report()
    elif args.list == "parsec":
        for name, cfg in BMK_CONFIG.items():
            if cfg["root"] == PARSEC_BIN_DIR: run_gem5(name, args.num)
        print_report()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
