#!/usr/bin/env python3
"""plot_charts.py — generate the 6 paper-faithful charts from m5out cells.

Reads each `m5out/<bench>_p<P>_d<D>_n<N>/` directory written by run_bmk.py:
  * stats.txt           — gem5 stats (IPC, simTicks, msg counts)
  * delegate_trace.txt  — SLICC DPRINTFs (C1 / C3 / M1 / B1 AMO counters)

Emits 6 PNGs into --out:
  chart1_ipc_per_config.png
  chart2_ipc_vs_amo_latency.png   (only if --amolat-glob matches)
  chart3_speedup.png
  chart4_packet_reduction.png
  chart5_cache_line_transfers.png
  chart6_amo_location.png

Usage:
  python3 plot_charts.py --m5out <PATH-TO-CLONE>/m5out --out ./plots
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

POLICY_NAMES = {0: "AllCentral", 1: "PinnedOwner", 2: "UnownedCentral",
                3: "AllMigrate", 4: "Delegato"}
NEAR_BASELINE_POLICY = 0  # AllCentral as the "near baseline" reference

# m5out/<bench>_p<P>_d<D>_n<N>           — main sweep cell
# m5out/<bench>_amolat<NS>ns_p<P>_d<D>_n<N>  — AMO-latency sweep cell
CELL_RE = re.compile(
    r"^(?P<bench>[a-z0-9_]+?)"
    r"(?:_amolat(?P<ns>[0-9.]+)ns)?"
    r"_p(?P<p>\d+)_d(?P<d>\d+)_n(?P<n>\d+)$"
)


def parse_stats(path: Path) -> dict:
    """Parse a gem5 stats.txt file. Returns flat {key: float} dict."""
    out = {}
    if not path.exists():
        return out
    with path.open(errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("---"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            key, val = parts[0], parts[1]
            try:
                out[key] = float(val)
            except ValueError:
                pass
    return out


def aggregate_cell(stats: dict) -> dict:
    """Pull the metrics we need from one cell's stats dict."""
    # IPC averaged across cores
    ipcs = [v for k, v in stats.items()
            if re.match(r"system\.processor\.cores\d+\.core\.ipc$", k)]
    ipc_avg = float(np.mean(ipcs)) if ipcs else 0.0

    sim_ticks = stats.get("simTicks", 0.0)

    # Total msg injections across all controllers (request+snoop+response+data)
    pkt_total = sum(
        v for k, v in stats.items()
        if re.search(r"\.(reqOut|snpOut|rspOut|datOut)\.m_msg_count$", k)
    )
    # Data-vnet only (cache-line transfers) — Chart 5
    data_msgs = sum(
        v for k, v in stats.items()
        if k.endswith(".datOut.m_msg_count")
    )
    return dict(ipc=ipc_avg, sim_ticks=sim_ticks,
                pkt_total=pkt_total, data_msgs=data_msgs)


def parse_delegate_trace(path: Path) -> dict:
    """Pull per-counter aggregate from the DelegateAMO/DynAMO trace.

    The SLICC code DPRINTFs lines like:
      [Counter A1]: + 1
      [Counter B1]: + 1
    so simply count occurrences per-name.
    """
    counts = defaultdict(int)
    if not path.exists():
        return dict(counts)
    pat = re.compile(r"\[Counter ([A-Za-z0-9_]+)\]")
    with path.open(errors="replace") as fh:
        for line in fh:
            m = pat.search(line)
            if m:
                counts[m.group(1)] += 1
    return dict(counts)


def scan_m5out(m5out_root: Path) -> dict:
    """Walk m5out/*/ and return {(bench, p, d, n, ns): cell_dict}."""
    rows = {}
    for cell in sorted(m5out_root.iterdir()):
        if not cell.is_dir():
            continue
        m = CELL_RE.match(cell.name)
        if not m:
            continue
        stats_path = cell / "stats.txt"
        trace_path = cell / "delegate_trace.txt"
        if not stats_path.exists():
            print(f"[skip] {cell.name}: no stats.txt", file=sys.stderr)
            continue
        agg = aggregate_cell(parse_stats(stats_path))
        agg["amo_counts"] = parse_delegate_trace(trace_path)
        agg["bench"] = m.group("bench")
        agg["p"] = int(m.group("p"))
        agg["d"] = int(m.group("d"))
        agg["n"] = int(m.group("n"))
        agg["ns"] = float(m.group("ns")) if m.group("ns") else None
        key = (agg["bench"], agg["p"], agg["d"], agg["n"], agg["ns"])
        rows[key] = agg
    return rows


def chart1_ipc_per_config(rows, out_path):
    """Chart 1 — IPC bar per (bench, policy), dyn=0 only."""
    benches = sorted({r["bench"] for r in rows.values() if r["ns"] is None})
    if not benches:
        print("[chart1] no rows; skipping")
        return
    policies = sorted({r["p"] for r in rows.values() if r["ns"] is None})
    fig, ax = plt.subplots(figsize=(max(6, 1.2 * len(benches)), 5))
    bar_w = 0.8 / len(policies)
    x = np.arange(len(benches))
    for i, p in enumerate(policies):
        ys = [rows.get((b, p, 0, 32, None), {}).get("ipc", 0) for b in benches]
        ax.bar(x + i * bar_w, ys, bar_w, label=POLICY_NAMES.get(p, f"p{p}"))
    ax.set_xticks(x + bar_w * (len(policies) - 1) / 2)
    ax.set_xticklabels(benches, rotation=30, ha="right")
    ax.set_ylabel("IPC (avg per core)")
    ax.set_title("Chart 1 — IPC per (config, bench)")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[chart1] {out_path}")


def chart2_ipc_vs_amo_latency(rows, out_path):
    """Chart 2 — IPC vs round-trip AMO latency."""
    points = [r for r in rows.values() if r["ns"] is not None]
    if not points:
        print("[chart2] no amolat cells; skipping (run with --amolat-glob)")
        return
    benches = sorted({r["bench"] for r in points})
    fig, ax = plt.subplots(figsize=(7, 5))
    for b in benches:
        bp = sorted([(r["ns"], r["ipc"]) for r in points if r["bench"] == b])
        if not bp:
            continue
        xs, ys = zip(*bp)
        ax.plot(xs, ys, "o-", label=b)
    ax.set_xlabel("Round-trip AMO latency (ns)")
    ax.set_ylabel("IPC")
    ax.set_title("Chart 2 — IPC vs AMO latency")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[chart2] {out_path}")


def chart3_speedup(rows, out_path):
    """Chart 3 — speedup vs Near baseline (policy=0)."""
    benches = sorted({r["bench"] for r in rows.values() if r["ns"] is None})
    policies = sorted({r["p"] for r in rows.values() if r["ns"] is None})
    if not benches or not policies:
        print("[chart3] no rows; skipping")
        return
    fig, ax = plt.subplots(figsize=(max(6, 1.2 * len(benches)), 5))
    bar_w = 0.8 / len(policies)
    x = np.arange(len(benches))
    for i, p in enumerate(policies):
        ys = []
        for b in benches:
            base = rows.get((b, NEAR_BASELINE_POLICY, 0, 32, None), {}) \
                .get("sim_ticks", 0)
            cur = rows.get((b, p, 0, 32, None), {}).get("sim_ticks", 0)
            ys.append(base / cur if cur > 0 and base > 0 else 0)
        ax.bar(x + i * bar_w, ys, bar_w, label=POLICY_NAMES.get(p, f"p{p}"))
    ax.axhline(1, color="black", lw=0.5, ls="--")
    ax.set_xticks(x + bar_w * (len(policies) - 1) / 2)
    ax.set_xticklabels(benches, rotation=30, ha="right")
    ax.set_ylabel(f"Speedup vs {POLICY_NAMES[NEAR_BASELINE_POLICY]}")
    ax.set_title("Chart 3 — Speedup per (config, bench)")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[chart3] {out_path}")


def chart4_packet_reduction(rows, out_path):
    """Chart 4 — % network-packet reduction vs Near baseline."""
    benches = sorted({r["bench"] for r in rows.values() if r["ns"] is None})
    policies = sorted({r["p"] for r in rows.values() if r["ns"] is None})
    if not benches or not policies:
        print("[chart4] no rows; skipping")
        return
    fig, ax = plt.subplots(figsize=(max(6, 1.2 * len(benches)), 5))
    bar_w = 0.8 / len(policies)
    x = np.arange(len(benches))
    for i, p in enumerate(policies):
        ys = []
        for b in benches:
            base = rows.get((b, NEAR_BASELINE_POLICY, 0, 32, None), {}) \
                .get("pkt_total", 0)
            cur = rows.get((b, p, 0, 32, None), {}).get("pkt_total", 0)
            ys.append(100 * (base - cur) / base if base > 0 else 0)
        ax.bar(x + i * bar_w, ys, bar_w, label=POLICY_NAMES.get(p, f"p{p}"))
    ax.axhline(0, color="black", lw=0.5)
    ax.set_xticks(x + bar_w * (len(policies) - 1) / 2)
    ax.set_xticklabels(benches, rotation=30, ha="right")
    ax.set_ylabel(f"% packets injected reduction vs "
                  f"{POLICY_NAMES[NEAR_BASELINE_POLICY]}")
    ax.set_title("Chart 4 — Network packets injected (% reduction)")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[chart4] {out_path}")


def chart5_cache_line_transfers(rows, out_path):
    """Chart 5 — absolute Data-vnet messages per (bench, policy)."""
    benches = sorted({r["bench"] for r in rows.values() if r["ns"] is None})
    policies = sorted({r["p"] for r in rows.values() if r["ns"] is None})
    if not benches or not policies:
        print("[chart5] no rows; skipping")
        return
    fig, ax = plt.subplots(figsize=(max(6, 1.2 * len(benches)), 5))
    bar_w = 0.8 / len(policies)
    x = np.arange(len(benches))
    for i, p in enumerate(policies):
        ys = [rows.get((b, p, 0, 32, None), {}).get("data_msgs", 0)
              for b in benches]
        ax.bar(x + i * bar_w, ys, bar_w, label=POLICY_NAMES.get(p, f"p{p}"))
    ax.set_xticks(x + bar_w * (len(policies) - 1) / 2)
    ax.set_xticklabels(benches, rotation=30, ha="right")
    ax.set_ylabel("Data-vnet messages (cache line transfers)")
    ax.set_title("Chart 5 — Cache line transfers")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[chart5] {out_path}")


def chart6_amo_location(rows, out_path):
    """Chart 6 — stacked bar of AMO location {Near=C3, Cent=C1, Mig=M1, Del=B1}.

    Plots Delegato (p=4) by default — the dynamic-routing policy where the
    breakdown actually varies. Static policies pin each bar to ~one bucket.
    """
    target_p = 4
    cells = [(b, r) for (b, p, d, n, ns), r in rows.items()
             if p == target_p and d == 0 and ns is None]
    if not cells:
        print("[chart6] no Delegato cells; skipping")
        return
    benches = sorted({b for b, _ in cells})
    cats = [("Near (C3)", "C3"),
            ("Centralized (C1)", "C1"),
            ("Migrating (M1)", "M1"),
            ("Delegated (B1)", "B1")]
    fig, ax = plt.subplots(figsize=(max(6, 1.2 * len(benches)), 5))
    x = np.arange(len(benches))
    bottom = np.zeros(len(benches))
    for label, key in cats:
        ys = []
        for b in benches:
            r = next((r for bb, r in cells if bb == b), {})
            ys.append(r.get("amo_counts", {}).get(key, 0))
        ys = np.array(ys, dtype=float)
        ax.bar(x, ys, 0.7, bottom=bottom, label=label)
        bottom += ys
    ax.set_xticks(x)
    ax.set_xticklabels(benches, rotation=30, ha="right")
    ax.set_ylabel("AMO event count")
    ax.set_title(f"Chart 6 — AMO location breakdown "
                 f"({POLICY_NAMES[target_p]})")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[chart6] {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--m5out", required=True, type=Path,
                    help="path to m5out/ directory containing per-cell subdirs")
    ap.add_argument("--out", required=True, type=Path,
                    help="output directory for the 6 PNGs")
    args = ap.parse_args()

    if not args.m5out.is_dir():
        print(f"error: {args.m5out} is not a directory", file=sys.stderr)
        sys.exit(2)
    args.out.mkdir(parents=True, exist_ok=True)

    rows = scan_m5out(args.m5out)
    if not rows:
        print(f"error: no parseable cells under {args.m5out}", file=sys.stderr)
        sys.exit(1)
    print(f"[scan] {len(rows)} cells parsed under {args.m5out}")

    chart1_ipc_per_config(rows, args.out / "chart1_ipc_per_config.png")
    chart2_ipc_vs_amo_latency(rows, args.out / "chart2_ipc_vs_amo_latency.png")
    chart3_speedup(rows, args.out / "chart3_speedup.png")
    chart4_packet_reduction(rows, args.out / "chart4_packet_reduction.png")
    chart5_cache_line_transfers(
        rows, args.out / "chart5_cache_line_transfers.png")
    chart6_amo_location(rows, args.out / "chart6_amo_location.png")


if __name__ == "__main__":
    main()
