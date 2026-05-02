#!/usr/bin/env python3
"""run_bmk.py — paper-faithful CHI/chiplet benchmark runner.

Adapted from main-branch toni's runner. Now invokes our paper-faithful
configs/example/chi_benchmark_se.py instead of the deprecated
configs/deprecated/example/se.py, and threads the chiplet flags
(--topology chiplet, --num-chiplets 2, --cores-per-chiplet 16,
--network garnet, --hns-per-chiplet 16, --delegato-enabled, etc.) per
chiplet.pdf §6.1 Table 3 (S1–S11 paper-faithful config).

Usage:
    # Single benchmark, single (policy, dyn) combo:
    python3 run_bmk.py -p splash/fmm -n 32 --policy 4 --dynamo 1

    # Whole sweep matrix (5 policies x 2 dyn-states for one bench):
    python3 run_bmk.py -p splash/fmm -n 32 --sweep

    # Whole list at one (policy, dyn):
    python3 run_bmk.py -l splash -n 32 --policy 4 --dynamo 0

    # Override paths:
    WKLDS_HOME=/some/path python3 run_bmk.py ...

Path conventions (overridable via env vars):
    WKLDS_HOME      — root containing wklds/ subdir (default = script dir)
    GEM5_ROOT       — gem5 source/build root (default = script dir)
    OUTPUT_ROOT     — where m5out subdirs land (default = $GEM5_ROOT/m5out)
"""
import subprocess
import argparse
import os
import sys
from pathlib import Path

# --- Path bootstrap (path-agnostic — works regardless of who cloned where) ---
SCRIPT_DIR = Path(__file__).resolve().parent
WKLDS_HOME = Path(os.environ.get("WKLDS_HOME", SCRIPT_DIR))
GEM5_ROOT = Path(os.environ.get("GEM5_ROOT", SCRIPT_DIR))
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_ROOT", GEM5_ROOT / "m5out"))

# Paper-faithful gem5 config (chi_benchmark_se.py with all S1–S11 flags)
GEM5_BIN = GEM5_ROOT / "build/ARM/gem5.opt"
GEM5_CONFIG = GEM5_ROOT / "configs/example/chi_benchmark_se.py"

# Workload locations (submodules — initialize with: git submodule update --init)
SPLASH_BASE = WKLDS_HOME / "wklds/Splash-4/Splash-4"
PARSEC_BASE = WKLDS_HOME / "wklds/parsec-benchmark"
PARSEC_BIN_DIR = WKLDS_HOME / "wklds/binaries/parsec"

# --- Benchmark Definitions ---
# "mode": "input"   — benchmark reads from stdin (we set --stdin <file>)
# "mode": "options" — benchmark takes argv (we set --args <string>)
BMK_CONFIG = {
    # --- Splash-4 Benchmarks ---
    "barnes": {
        "root": SPLASH_BASE, "cmd": "barnes/BARNES",
        "mode": "input",
        "args": "barnes/inputs/n16384-p{N}",
    },
    "fmm": {
        "root": SPLASH_BASE, "cmd": "fmm/FMM",
        "mode": "input",
        "args": "fmm/inputs/input.{N}.16384",
    },
    "fft": {
        "root": SPLASH_BASE, "cmd": "fft/FFT",
        "mode": "options", "args": "-p{N} -m16",
    },
    "cholesky": {
        "root": SPLASH_BASE, "cmd": "cholesky/CHOLESKY",
        "mode": "options",
        "args": "-p{N} -f {SPLASH_BASE}/cholesky/inputs/tk15.O",
    },
    "lu_cont": {
        "root": SPLASH_BASE, "cmd": "lu-contiguous_blocks/LU-CONT",
        "mode": "options", "args": "-p{N} -n512",
    },
    "lu_noncont": {
        "root": SPLASH_BASE, "cmd": "lu-non_contiguous_blocks/LU-NOCONT",
        "mode": "options", "args": "-p{N} -n512",
    },
    "ocean_cont": {
        "root": SPLASH_BASE, "cmd": "ocean-contiguous_partitions/OCEAN-CONT",
        "mode": "options", "args": "-n258 -p{N}",
    },
    "ocean_noncont": {
        "root": SPLASH_BASE, "cmd": "ocean-non_contiguous_partitions/OCEAN-NOCONT",
        "mode": "options", "args": "-n258 -p{N}",
    },
    "radiosity": {
        "root": SPLASH_BASE, "cmd": "radiosity/RADIOSITY",
        "mode": "options", "args": "-p {N} -batch -room",
    },
    "radix": {
        "root": SPLASH_BASE, "cmd": "radix/RADIX",
        "mode": "options", "args": "-p{N}",
    },
    "raytrace_sp": {
        "root": SPLASH_BASE, "cmd": "raytrace/RAYTRACE",
        "mode": "options",
        "args": "-p{N} {SPLASH_BASE}/raytrace/inputs/car.env",
    },
    "volrend": {
        "root": SPLASH_BASE, "cmd": "volrend/VOLREND",
        "mode": "options",
        "args": "{N} {SPLASH_BASE}/volrend/inputs/head",
    },
    "water_nsquared": {
        "root": SPLASH_BASE, "cmd": "water-nsquared/WATER-NSQUARED",
        "mode": "input",
        "args": "water-nsquared/inputs/n512-p{N}",
    },
    "water_spatial": {
        "root": SPLASH_BASE, "cmd": "water-spatial/WATER-SPATIAL",
        "mode": "input",
        "args": "water-spatial/inputs/n512-p{N}",
    },

    # --- PARSEC Benchmarks ---
    "blackscholes": {
        "root": PARSEC_BIN_DIR, "cmd": "blackscholes",
        "mode": "options",
        "args": "{N} {PARSEC_BASE}/pkgs/apps/blackscholes/inputs/in_4K.txt prices.txt",
    },
    "fluidanimate": {
        "root": PARSEC_BIN_DIR, "cmd": "fluidanimate",
        "mode": "options",
        "args": "{N} 5 {PARSEC_BASE}/pkgs/apps/fluidanimate/inputs/in_35K.fluid out.fluid",
    },
    "freqmine": {
        "root": PARSEC_BIN_DIR, "cmd": "freqmine",
        "mode": "options",
        "args": "{PARSEC_BASE}/pkgs/apps/freqmine/inputs/kosarak_250k.dat 11000",
    },
    "x264": {
        "root": PARSEC_BIN_DIR, "cmd": "x264",
        "mode": "options",
        "args": "--quiet --qp 20 --partitions b8x8,i4x4 --ref 5 --direct auto "
                "--b-pyramid --weightb --mixed-refs --no-fast-pskip --me umh "
                "--subme 7 --analyse b8x8,i4x4 --threads {N} -o eledream.264 "
                "{PARSEC_BASE}/pkgs/apps/x264/inputs/eledream_640x360_8.y4m",
    },
    "canneal": {
        "root": PARSEC_BIN_DIR, "cmd": "canneal",
        "mode": "options",
        "args": "{N} 10000 2000 "
                "{PARSEC_BASE}/pkgs/kernels/canneal/inputs/100000.nets 32",
    },
    "dedup": {
        "root": PARSEC_BIN_DIR, "cmd": "dedup",
        "mode": "options",
        "args": "-c -p -v -t {N} -i "
                "{PARSEC_BASE}/pkgs/kernels/dedup/inputs/media.dat "
                "-o output.dat.ddp",
    },
    "swaptions": {
        "root": PARSEC_BIN_DIR, "cmd": "swaptions",
        "mode": "options",
        "args": "-ns 16 -sm 5000 -nt {N}",
    },
    "streamcluster": {
        "root": PARSEC_BIN_DIR, "cmd": "streamcluster",
        "mode": "options",
        "args": "10 20 32 4096 4096 1000 none output.txt {N}",
    },
}

POLICY_NAMES = {0: "AllCentral", 1: "PinnedOwner", 2: "UnownedCentral",
                3: "AllMigrate", 4: "Delegato"}
results_summary = []


def expand_args(template: str, n: int) -> str:
    """Substitute {N}, {SPLASH_BASE}, {PARSEC_BASE} into a bench's arg template."""
    return (template
            .replace("{N}", str(n))
            .replace("{SPLASH_BASE}", str(SPLASH_BASE))
            .replace("{PARSEC_BASE}", str(PARSEC_BASE)))


def run_gem5(name, cpu_count, policy, dynamo, extra_flags=None):
    """Invoke gem5 once for (bench, policy, dyn). Output to per-cell subdir.

    extra_flags: optional list of extra CLI args passed to chi_benchmark_se.py
                 (e.g., ["--zero-amo-traversal", "--atomic-op-latency", "0",
                  "--inter-chiplet-link-latency", "1"]).
    """
    extra_flags = extra_flags or []
    clean = name.split("/")[-1].lower()
    if clean not in BMK_CONFIG:
        print(f"Error: '{clean}' (from '{name}') not in BMK_CONFIG.")
        return

    cfg = BMK_CONFIG[clean]
    abs_cmd = Path(cfg["root"]) / cfg["cmd"]
    out_dir = OUTPUT_ROOT / f"{clean}_p{policy}_d{dynamo}_n{cpu_count}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resume support: skip if a previous run completed.
    stats = out_dir / "stats.txt"
    stdout_log = out_dir / "gem5_stdout.txt"
    if stats.exists() and stdout_log.exists():
        try:
            if "Exiting @ tick" in stdout_log.read_text(errors="replace"):
                print(f"[skip] {clean} p{policy} d{dynamo} — already complete")
                results_summary.append({
                    "name": clean, "policy": policy, "dyn": dynamo,
                    "status": "SKIPPED (already done)"
                })
                return
        except OSError:
            pass

    # --- Construct chi_benchmark_se.py command ---
    cmd = [
        str(GEM5_BIN),
        "--debug-flags=DelegateAMO,DynAMO,Delegato",
        "--debug-file=delegate_trace.txt",
        f"--outdir={out_dir}",
        str(GEM5_CONFIG),
        f"--binary={abs_cmd}",
        f"--num-cores={cpu_count}",
        f"--hn-amo-policy={policy}",
        # Paper-faithful chiplet topology (S1–S11)
        "--topology=chiplet",
        "--num-chiplets=2",
        "--cores-per-chiplet=16",
        "--network=garnet",
        "--hns-per-chiplet=16",
        "--mesh-rows=4",
        "--mesh-cols=4",
        "--delegato-enabled",   # always on; AMO routing decided by --hn-amo-policy
    ]
    if dynamo:
        cmd.append("--dynamo-enabled")

    # Bench inputs: stdin-mode for SPLASH benches that read from stdin,
    # options-mode for everything else.
    raw = expand_args(cfg["args"], cpu_count)
    if cfg["mode"] == "input":
        abs_input = Path(cfg["root"]) / raw
        cmd.append(f"--stdin={abs_input}")
    else:
        cmd.append(f"--args={raw}")

    cmd.extend(extra_flags)

    label = f"{clean} pol={policy}({POLICY_NAMES.get(policy, '?')}) " \
            f"dyn={dynamo} N={cpu_count}"
    print(f"\n>>> {label}")
    print(f"    outdir={out_dir}")
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace")
        # Persist stdout for diagnosis
        stdout_log.write_text(proc.stdout)
        out = proc.stdout

        if "Exiting @ tick" in out:
            status = "SUCCESS"
        elif "fatal: Out of memory" in out:
            status = "FAILED (OOM)"
        elif "Failed to open file" in out or "No such file" in out:
            status = "FAILED (Path/File Error)"
        elif proc.returncode != 0:
            status = f"FAILED (exit {proc.returncode})"
        else:
            status = "FAILED (no Exiting @ tick — check output)"
            print("\n--- last 15 lines of gem5 output ---")
            print("\n".join(out.splitlines()[-15:]))

        results_summary.append({
            "name": clean, "policy": policy, "dyn": dynamo, "status": status
        })
        print(f"    -> {status}")
    except KeyboardInterrupt:
        sys.exit(1)


def print_report():
    print("\n" + "=" * 70)
    print(f"{'Workload':<18} | {'Policy':<14} | {'Dyn':<3} | {'Status':<25}")
    print("-" * 70)
    for res in results_summary:
        pname = POLICY_NAMES.get(res["policy"], "?")
        print(f"{res['name']:<18} | {res['policy']} ({pname:<10}) | "
              f"{res['dyn']:<3} | {res['status']:<25}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Paper-faithful CHI chiplet benchmark runner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("-l", "--list", type=str,
                        help="splash | parsec — run every bench in the suite")
    parser.add_argument("-p", "--prog", type=str,
                        help="suite/bench (e.g., parsec/fluidanimate, splash/fmm)")
    parser.add_argument("-n", "--num", type=int, default=32,
                        help="num cores (paper Table 3 = 32)")
    parser.add_argument("--policy", type=int, default=4,
                        help="HN AMO policy: 0=AllCentral 1=PinnedOwner "
                             "2=UnownedCentral 3=AllMigrate 4=Delegato")
    parser.add_argument("--dynamo", type=int, default=0,
                        help="DynAMO predictor: 0=off 1=on")
    parser.add_argument("--sweep", action="store_true",
                        help="Run the full 5x2 (policy x dyn) matrix on the "
                             "selected bench(es) instead of a single cell")
    parser.add_argument("--zero-amo-traversal", action="store_true",
                        help="Pass --zero-amo-traversal (S12; chart #2 0-ns)")
    parser.add_argument("--atomic-op-latency", type=int, default=None,
                        help="Override --atomic-op-latency (chart #2 sweep)")
    parser.add_argument("--inter-link-lat", type=int, default=None,
                        help="Override --inter-chiplet-link-latency (chart #2)")
    args = parser.parse_args()

    extra = []
    if args.zero_amo_traversal:
        extra.append("--zero-amo-traversal")
    if args.atomic_op_latency is not None:
        extra.extend(["--atomic-op-latency", str(args.atomic_op_latency)])
    if args.inter_link_lat is not None:
        extra.extend(["--inter-chiplet-link-latency", str(args.inter_link_lat)])

    def cells():
        if args.sweep:
            return [(p, d) for p in (0, 1, 2, 3, 4) for d in (0, 1)]
        return [(args.policy, args.dynamo)]

    if args.prog:
        for pol, dyn in cells():
            run_gem5(args.prog, args.num, pol, dyn, extra)
        print_report()
    elif args.list == "splash":
        for name, cfg in BMK_CONFIG.items():
            if cfg["root"] == SPLASH_BASE:
                for pol, dyn in cells():
                    run_gem5(name, args.num, pol, dyn, extra)
        print_report()
    elif args.list == "parsec":
        for name, cfg in BMK_CONFIG.items():
            if cfg["root"] == PARSEC_BIN_DIR:
                for pol, dyn in cells():
                    run_gem5(name, args.num, pol, dyn, extra)
        print_report()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
