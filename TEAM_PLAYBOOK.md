# Team Playbook — Paper-Faithful Chiplet Charts

Each teammate runs an independent share of `run_bmk.py` invocations. The
results write into per-cell subdirs of `m5out/` and can be collated for
plotting at the end.

> **Config in use**: paper-faithful (chiplet.pdf §6.1 Table 3 + author errata),
> S1–S11 stages locked in: 32 OoO cores @ 3 GHz / 2 chiplets × 16 cores,
> 4×4 mesh per chiplet, tile-coupled, 16 HNs/chiplet (32 LLC slices), real
> mostly-exclusive L3 (1 MiB/slice, 16-way, 12+2 cyc), NUMA-partitioned HN
> address mapping, 4 inter-chiplet bisection links @ 50 ns, 8-channel DDR5
> 8 GiB, 3 GHz CPU / 2 GHz NoC clock split, ni_flit_size=64 B (~512 GB/s
> aggregate cross-chiplet). Validated. **No S12 in this code.**

---

## One-time setup (each teammate)

Replace `<PATH-TO-CLONE>` with where you cloned the repo.

```bash
# 1. Clone + check out chiplet branch
git clone <REPO_URL> <PATH-TO-CLONE>
cd <PATH-TO-CLONE>
git checkout chiplet
git submodule update --init --recursive    # pulls wklds/Splash-4, wklds/parsec-benchmark

# 2. Build gem5 (~15 min; only once)
scons-3 USE_HDF5=0 -j 16 build/ARM/gem5.opt
# On ECN, prepend the gcc 8.3 toolchain first:
#   export PATH=/package/gcc/8.3.0/bin:$PATH
#   export LD_LIBRARY_PATH=/package/gcc/8.3.0/lib64:$LD_LIBRARY_PATH

# 3. Sanity check the runner
python3 run_bmk.py --help
```

**No path edits needed** — `run_bmk.py` resolves all paths from its own
location. If your wklds/ is somewhere else, set `WKLDS_HOME=<PATH-TO-WKLDS-PARENT>`.

---

## Chart-by-chart work assignments

There are 6 charts. Each teammate owns one chart. **Charts 1, 3, 4, 5, 6
share one underlying sweep** — running it once produces data for all five.
Chart 2 needs its own sweep. We split as follows:

| Teammate | Chart # | Owns benchmarks | Cells | Est. wall |
|---|---|---|---|---|
| 1 | Chart 1, 3 (IPC bar + Speedup bar) | `splash/fmm`, `splash/fft`, `parsec/fluidanimate`, `parsec/swaptions` | 40 | ~10–20 hr |
| 2 | Chart 4, 6 (Network packets + AMO location) | `splash/barnes`, `splash/radix`, `parsec/canneal`, `parsec/streamcluster` | 40 | ~10–20 hr |
| 3 | Chart 5 (Cache line transfers) | `splash/lu_cont`, `splash/water_nsquared`, `parsec/blackscholes`, `parsec/freqmine` | 40 | ~10–20 hr |
| 4 | Chart 2 (IPC vs AMO latency) — **non-zero points only** | `splash/fmm` and `parsec/fluidanimate` × 4 latency points (12.5 / 25 / 50 / 100 ns) | 8 | ~3–5 hr |

> The **0-ns "ideal AMO" point on Chart 2** is being run by claude separately
> (requires the in-progress S12 patch — kept off the chiplet branch until
> it builds and validates).

Charts 1, 3, 4, 5, 6 all read from the same per-cell `stats.txt` and
`comparison_results_*.txt` outputs — so even though we split benchmarks
across teammates, every chart will have data for all 12 sweep benches at
the end. The "owns" column above is just to give each chart a clear owner
for the plotting + write-up; pick benches you care about for your chart.

---

## Teammate 1 — Charts 1 + 3 (IPC bar + Speedup bar)

**Goal**: produce IPC values + simTicks for FMM, FFT (SPLASH), fluidanimate,
swaptions (PARSEC), each across 5 policies × 2 dyn-states. Charts 1 (raw
IPC) and 3 (speedup vs Near baseline = lowest-IPC policy per bench) come
from the same data.

```bash
# Save as: ~/run_t1.sh
#!/usr/bin/env bash
set -euo pipefail
cd <PATH-TO-CLONE>

# (ECN) toolchain
export PATH=/package/gcc/8.3.0/bin:$PATH
export LD_LIBRARY_PATH=/package/gcc/8.3.0/lib64:${LD_LIBRARY_PATH:-}

for bench in splash/fmm splash/fft parsec/fluidanimate parsec/swaptions; do
  python3 run_bmk.py -p "$bench" -n 32 --sweep
done
```

Run as: `nohup bash ~/run_t1.sh > ~/run_t1.log 2>&1 &` then
`tail -f ~/run_t1.log`.

40 cells × 30–60 min ≈ **10–20 hours wall-clock** (resumable — re-run
re-skips completed cells).

**Where my data lands**: `<PATH-TO-CLONE>/m5out/<bench>_p<P>_d<D>_n32/stats.txt`
and `comparison_results_*.txt`.

---

## Teammate 2 — Charts 4 + 6 (Network packets + AMO location)

**Goal**: produce network-packet counts + AMO-event counts (C1 / C3 / M1 /
B1) for benches with high cross-chiplet traffic (where policies should
differentiate visibly).

```bash
# Save as: ~/run_t2.sh
#!/usr/bin/env bash
set -euo pipefail
cd <PATH-TO-CLONE>
export PATH=/package/gcc/8.3.0/bin:$PATH
export LD_LIBRARY_PATH=/package/gcc/8.3.0/lib64:${LD_LIBRARY_PATH:-}

for bench in splash/barnes splash/radix parsec/canneal parsec/streamcluster; do
  python3 run_bmk.py -p "$bench" -n 32 --sweep
done
```

Same `nohup` pattern. 40 cells, ~10–20 hr.

---

## Teammate 3 — Chart 5 (Cache line transfers)

**Goal**: capture Data-vnet message counts per (bench, policy) so we can
plot absolute and Near-normalized cache-line-transfer counts. Pick a
ping-pong-prone bench (lu_cont) and contrast against compute-bound ones.

```bash
# Save as: ~/run_t3.sh
#!/usr/bin/env bash
set -euo pipefail
cd <PATH-TO-CLONE>
export PATH=/package/gcc/8.3.0/bin:$PATH
export LD_LIBRARY_PATH=/package/gcc/8.3.0/lib64:${LD_LIBRARY_PATH:-}

for bench in splash/lu_cont splash/water_nsquared parsec/blackscholes parsec/freqmine; do
  python3 run_bmk.py -p "$bench" -n 32 --sweep
done
```

Same pattern. 40 cells, ~10–20 hr.

---

## Teammate 4 — Chart 2 (IPC vs AMO latency, non-zero points)

**Goal**: 4 of 5 points on the AMO-latency curve. Each point is a
(`atomic-op-latency`, `inter-chiplet-link-latency`) pair that adds up to
the desired round-trip ns. Claude does the 0-ns point separately.

```bash
# Save as: ~/run_t4.sh
#!/usr/bin/env bash
set -euo pipefail
cd <PATH-TO-CLONE>
export PATH=/package/gcc/8.3.0/bin:$PATH
export LD_LIBRARY_PATH=/package/gcc/8.3.0/lib64:${LD_LIBRARY_PATH:-}

# round-trip-ns : atomic-op-latency : inter-link-lat
for cfg in "12.5:1:25" "25:2:50" "50:4:100" "100:8:200"; do
  IFS=":" read -r ns aop ilat <<< "$cfg"
  for bench in splash/fmm parsec/fluidanimate; do
    python3 run_bmk.py -p "$bench" -n 32 \
      --policy 4 --dynamo 0 \
      --atomic-op-latency "$aop" \
      --inter-link-lat "$ilat"
    # rename outdir so multiple latency points don't collide
    mv -n "m5out/$(basename "$bench")_p4_d0_n32" \
          "m5out/$(basename "$bench")_amolat${ns}ns_p4_d0_n32" 2>/dev/null || true
  done
done
```

8 cells, ~3–5 hr.

---

## Status check (anyone)

```bash
cd <PATH-TO-CLONE>
ls -la m5out/ | wc -l           # how many cell dirs exist
find m5out -name "stats.txt" | wc -l   # how many completed
tail ~/run_t<your-num>.log
```

---

## When everyone's done — collation + plotting

One person runs:

```bash
cd <PATH-TO-CLONE>

# 1. Collect every comparison_results_*.txt (per-bench CSVs from extract_counters)
mkdir -p benchmark/paper_faithful_charts/raw
find m5out -name "comparison_results_*.txt" \
  -exec cp {} benchmark/paper_faithful_charts/raw/ \;

# 2. (If you ran on ECN) pull raw/ to your laptop:
#    scp -r <ECN-USER>@<ECN-HOST>:<PATH-TO-CLONE>/benchmark/paper_faithful_charts/raw/ ./benchmark/paper_faithful_charts/raw/

# 3. Plot (matplotlib script lives in benchmark/paper_faithful_charts/scripts/)
python3 benchmark/paper_faithful_charts/scripts/plot_charts.py \
  --raw benchmark/paper_faithful_charts/raw \
  --out benchmark/paper_faithful_charts/plots
ls benchmark/paper_faithful_charts/plots/   # 6 PNGs
```

The plot script generates: `chart1_ipc_per_config.png`, `chart2_ipc_vs_amo_latency.png`,
`chart3_speedup.png`, `chart4_packet_reduction.png`, `chart5_cache_line_transfers.png`,
`chart6_amo_location.png`.

---

## Reference: paper Table 3 sanity checks

If you want to spot-check that your runs use the paper-faithful config,
the gem5 elaboration prints these at startup:

```
[MemoryEvidence] mem_type=ddr5_8ch size=8GiB
[O3MicroarchEvidence] cpu_type=o3 ROB=224 LQ=76 SQ=58 fetchW=8 issueW=13
[ClockEvidence] cpu_clk=3GHz noc_clk=2GHz
[ChipletEvidence] HN0(chiplet=0).addr_range = 0:4294967296:0:64    # ... 31 more lines
[L3Evidence] HN<N>(chiplet=<C>).cache = L3(1MiB,16-way,12+2cy,mostly-excl)
[GarnetMeshEvidence] num_chiplets=2 mesh_rows=4 mesh_cols=4 routers_per_chiplet=16 ...
                     flit_size=64B per_link_bw=128GB/s cross_chiplet_bw=512GB/s ...
[GarnetMeshEvidence] bisection row=0: router3 <-> router16 latency=100cy   # ...rows 1-3
```

Look for these in the first ~200 lines of `m5out/<cell>/gem5_stdout.txt`.

---

## Troubleshooting

- **"BARNES: cannot read input"** — that bench reads from stdin; the runner
  passes `--stdin <path>` to chi_benchmark_se.py for "input"-mode benches.
  Make sure `wklds/Splash-4/Splash-4/barnes/inputs/n16384-p32` exists
  (init the submodule with `git submodule update --init --recursive`).
- **"binary not found"** — the PARSEC binaries live in `wklds/binaries/parsec/`.
  Check `ls wklds/binaries/parsec/` shows the bench you're running.
- **Job dies mid-sweep** — relaunch the same script. Cells with a complete
  `stats.txt` + `Exiting @ tick` in stdout are auto-skipped.
- **Cell takes >1 hour** — the runner doesn't hard-kill, but you can
  Ctrl-C and re-launch. Increase the per-cell timeout if you hit it
  consistently.
- **Out of memory on ECN** — each gem5 process uses ~9 GB RAM at paper
  scale. If multiple teammates run concurrently and OOM, stagger by
  ~30 min so each individual cell has the host's memory headroom.
