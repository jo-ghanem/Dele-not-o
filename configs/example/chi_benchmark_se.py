"""
CHI benchmark runner for SE mode.
Runs any statically-linked ARM binary under the CHI PrivateL1PrivateL2
cache hierarchy with configurable hn_amo_policy for delegated AMO testing.

Usage:
    build/ARM/gem5.opt configs/example/chi_benchmark_se.py \
        --binary /path/to/benchmark \
        --args "-p 4 -m 10" \
        --num-cores 4 \
        --hn-amo-policy 1
"""

import argparse
import sys

import m5
from m5.objects import *

from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.simple_board import SimpleBoard
from gem5.components.cachehierarchies.chi.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.cachehierarchies.chi.dual_chiplet_private_l1_l2_cache_hierarchy import (
    DualChipletPrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.memory import SingleChannelDDR3_1600
from gem5.components.memory.memory import ChanneledMemory
from m5.objects import DDR5_8400_4x8
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import BinaryResource
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

requires(
    isa_required=ISA.ARM,
    coherence_protocol_required=CoherenceProtocol.CHI,
)

parser = argparse.ArgumentParser(
    description="Run an ARM SE binary with CHI cache hierarchy"
)
parser.add_argument(
    "--binary", type=str, required=True,
    help="Path to statically-linked ARM binary",
)
parser.add_argument(
    "--args", type=str, default="",
    help="Arguments to pass to the binary (space-separated string)",
)
parser.add_argument("--num-cores", type=int, default=4)
parser.add_argument("--hn-amo-policy", type=int, default=0,
    help="0=All-Central, 1=Pinned-Owner, 2=Unowned-Central, 3=All-Migrate")
parser.add_argument("--cpu-type", type=str, default="o3",
    choices=["timing", "atomic", "o3"],
    help="CPU type (default 'o3' for paper-faithful Neoverse-V1-class OoO; "
         "'timing' for fast in-order regression)")
# S2: Neoverse-V1-class O3 microarch knobs (chiplet.pdf §6.1 Table 3).
# Applied only when --cpu-type=o3; pattern from configs/example/gem5_library/
# fdp-hello.py — iterate processor.get_cores() and set c.core.<param>.
parser.add_argument("--cpu-rob", type=int, default=224,
    help="(o3 only) Reorder buffer entries (paper Table 3: 224)")
parser.add_argument("--cpu-lq", type=int, default=76,
    help="(o3 only) Load queue entries (paper Table 3: 76)")
parser.add_argument("--cpu-sq", type=int, default=58,
    help="(o3 only) Store queue entries (paper Table 3: 58)")
parser.add_argument("--cpu-fetch-width", type=int, default=8,
    help="(o3 only) Fetch / decode / commit width (paper Table 3: 8)")
parser.add_argument("--cpu-issue-width", type=int, default=13,
    help="(o3 only) Dispatch / issue width (paper Table 3: 13)")
parser.add_argument("--l1d-size", type=str, default="64KiB",
    help="Private L1D size per core (paper Table 3: 64 KiB, 4-way, 3-cyc).")
parser.add_argument("--l1i-size", type=str, default="64KiB",
    help="Private L1I size per core (paper Table 3: 64 KiB, 4-way, 3-cyc).")
parser.add_argument("--l2-size", type=str, default="1MiB",
    help="Private L2 size per core (paper Table 3: 1MiB, 8-way, 8-cyc).")
parser.add_argument("--mem-size", type=str, default="8GiB",
    help="Total physical memory (paper Table 3: 8 GiB).")
parser.add_argument("--mem-type", type=str, default="ddr5_8ch",
    choices=["ddr5_8ch", "ddr3_1ch"],
    help="Memory model. ddr5_8ch (default) = 8-channel DDR5_8400 per "
         "chiplet.pdf §6.1 Table 3 (~248 GB/s aggregate). ddr3_1ch = "
         "single-channel DDR3-1600 (legacy regression baseline).")
parser.add_argument("--dynamo-enabled", action="store_true",
    help="Enable DynAMO-Reuse L1 predictor (dynamo.pdf ISCA'23 §5)")
parser.add_argument("--dynamo-threshold", type=int, default=1,
    help="DynAMO confidence threshold (near iff conf > threshold)")
parser.add_argument("--dynamo-variant", type=int, default=0,
    help="0=Reuse-PN (default), 1=Reuse-UN, 2=metric")
parser.add_argument("--delegato-enabled", action="store_true",
    help="Enable Delegato HN-side predictor (chiplet.pdf §5.3)")
parser.add_argument("--delegato-variant", type=int, default=0,
    help="0=FSM, 1=AlwaysDelegate, 2=AlwaysMigrate, 3=AlwaysCentralize")
parser.add_argument("--atomic-op-latency", type=int, default=4,
    help="Cycles for atomic ALU operation (0 = ALU-free ceiling for baseline F)")
parser.add_argument("--topology", type=str, default="single",
    choices=["single", "chiplet"],
    help="single = stock single-HN (regression baseline); "
         "chiplet = 2-HN dual-chiplet hierarchy with ChipletPt2Pt")
parser.add_argument("--num-chiplets", type=int, default=2,
    help="(chiplet topology only) number of chiplets")
parser.add_argument("--cores-per-chiplet", type=int, default=16,
    help="(chiplet topology only) cores per chiplet")
parser.add_argument("--inter-chiplet-link-latency", type=int, default=100,
    help="(chiplet topology only) cross-chiplet link latency in cycles "
         "(default 100 = 50 ns @ 2 GHz NoC, per chiplet.pdf §6.1)")
parser.add_argument("--network", type=str, default="simple",
    choices=["simple", "garnet"],
    help="(chiplet topology only) network model: simple = ChipletPt2Pt "
         "single-edge approximation (default); garnet = ChipletGarnetMesh "
         "paper-faithful HeteroGarnet 2D mesh per chiplet (latency-faithful, "
         "bandwidth simplified per GARNET_TOPOLOGY_EVIDENCE.md §2a)")
parser.add_argument("--mesh-rows", type=int, default=4,
    help="(chiplet+garnet only) per-chiplet mesh row count (default 4)")
parser.add_argument("--mesh-cols", type=int, default=4,
    help="(chiplet+garnet only) per-chiplet mesh column count "
         "(default 4 = paper-faithful 4×4 per chiplet, system view 8×4 "
         "after S9 bisection bridges; chiplet.pdf author errata)")
parser.add_argument("--bridge-router-idx", type=int, default=0,
    help="(chiplet+garnet only) per-chiplet router index that hosts the "
         "inter-chiplet bridge IntLink (default 0 = top-left corner)")
parser.add_argument("--hns-per-chiplet", type=int, default=1,
    help="(chiplet topology only) HN/L3 slices per chiplet. Paper §6.1 / "
         "Table 3 specifies 16 (= 32 total LLC slices for 2 chiplets). "
         "Default 1 preserves the v1 mesh single-HN-per-chiplet baseline. "
         "Must be a power of 2 (intlvBits requirement).")
parser.add_argument("--policy-type", type=int, default=1,
    help="L1-side AMO policy: 0=All-Near, 1=Unique-Near (default), "
         "2=Present-Near, 5=All-Central (Stage C — force every AMO to HN)")
# S3: Real LLC at each HN slice. Default 1MiB matches chiplet.pdf §6.1
# Table 3 (32 slices × 1 MiB, 16-way, 12-cyc, mostly-exclusive). Set
# --l3-size "" (empty) to keep the legacy snoop-filter-only HN.
parser.add_argument("--l3-size", type=str, default="1MiB",
    help="(chiplet topology only) Per-HN L3 slice size. Empty/None = "
         "legacy snoop-filter-only HN (no data caching). Paper Table 3: 1MiB.")
parser.add_argument("--l3-assoc", type=int, default=16,
    help="(chiplet topology only) L3 associativity. Paper Table 3: 16.")
parser.add_argument("--l3-data-latency", type=int, default=12,
    help="(chiplet topology only) L3 data access latency in cycles. "
         "Paper Table 3: 12.")
parser.add_argument("--l3-tag-latency", type=int, default=2,
    help="(chiplet topology only) L3 tag access latency in cycles. Default 2.")
# S10: split CPU vs NoC clock domains. Paper §6.1 Table 3 has CPU @ 3 GHz
# and NoC+LLC @ 2 GHz. We pass cpu_clk_freq to SimpleBoard (which becomes
# the default clk_domain for cores/L1/L2/sequencers) and noc_clk_freq to
# the cache hierarchy so it can build a separate SrcClockDomain attached
# to HN/L3/Garnet network/memory controllers.
parser.add_argument("--cpu-clk-freq", type=str, default="3GHz",
    help="CPU clock domain (paper Table 3: 3 GHz). Applied to L1/L2/cores.")
parser.add_argument("--noc-clk-freq", type=str, default="2GHz",
    help="NoC + LLC clock domain (paper Table 3: 2 GHz). Applied to "
         "HN/L3/Garnet network/memory controllers.")
args = parser.parse_args()

cpu_type_map = {
    "timing": CPUTypes.TIMING,
    "atomic": CPUTypes.ATOMIC,
    "o3": CPUTypes.O3,
}

if args.topology == "chiplet":
    cache_hierarchy = DualChipletPrivateL1PrivateL2CacheHierarchy(
        l1i_size=args.l1i_size,
        l1i_assoc=4,
        l1d_size=args.l1d_size,
        l1d_assoc=4,
        l2_size=args.l2_size,
        l2_assoc=8,
        hn_amo_policy=args.hn_amo_policy,
        atomic_op_latency=args.atomic_op_latency,
        policy_type=args.policy_type,
        dynamo_enabled=args.dynamo_enabled,
        dynamo_threshold=args.dynamo_threshold,
        dynamo_variant=args.dynamo_variant,
        delegato_enabled=args.delegato_enabled,
        delegato_variant=args.delegato_variant,
        num_chiplets=args.num_chiplets,
        cores_per_chiplet=args.cores_per_chiplet,
        inter_link_lat=args.inter_chiplet_link_latency,
        network=args.network,
        mesh_rows=args.mesh_rows,
        mesh_cols=args.mesh_cols,
        bridge_router_idx=args.bridge_router_idx,
        hns_per_chiplet=args.hns_per_chiplet,
        l3_size=(args.l3_size if args.l3_size else None),
        l3_assoc=args.l3_assoc,
        l3_data_latency=args.l3_data_latency,
        l3_tag_latency=args.l3_tag_latency,
        noc_clk_freq=args.noc_clk_freq,
    )
else:
    cache_hierarchy = PrivateL1PrivateL2CacheHierarchy(
        l1i_size=args.l1i_size,
        l1i_assoc=4,
        l1d_size=args.l1d_size,
        l1d_assoc=4,
        l2_size=args.l2_size,
        l2_assoc=8,
        hn_amo_policy=args.hn_amo_policy,
        atomic_op_latency=args.atomic_op_latency,
        policy_type=args.policy_type,
        dynamo_enabled=args.dynamo_enabled,
        dynamo_threshold=args.dynamo_threshold,
        dynamo_variant=args.dynamo_variant,
        delegato_enabled=args.delegato_enabled,
        delegato_variant=args.delegato_variant,
    )

# S8: paper-faithful 8-channel DDR5 (chiplet.pdf §6.1 Table 3 — paper text
# "DDR8" is a typo for DDR5 per author confirmation). 64-byte interleave
# matches the cache line size and the existing SingleChannel*/DualChannel*
# helpers in gem5/components/memory/. ddr3_1ch path preserves the legacy
# regression baseline.
if args.mem_type == "ddr5_8ch":
    memory = ChanneledMemory(DDR5_8400_4x8, 8, 64, size=args.mem_size)
elif args.mem_type == "ddr3_1ch":
    memory = SingleChannelDDR3_1600(size=args.mem_size)
else:
    raise ValueError(f"unknown --mem-type {args.mem_type!r}")
print(f"[MemoryEvidence] mem_type={args.mem_type} size={args.mem_size}", flush=True)

processor = SimpleProcessor(
    cpu_type=cpu_type_map[args.cpu_type],
    isa=ISA.ARM,
    num_cores=args.num_cores,
)

# S2 — apply Neoverse-V1-class microarch params per chiplet.pdf §6.1 Table 3.
# Pattern: configs/example/gem5_library/fdp-hello.py — set fields on c.core
# (the underlying ArmO3CPU SimObject wrapped by SimpleCore).
if args.cpu_type == "o3":
    for c in processor.get_cores():
        c.core.numROBEntries = args.cpu_rob
        c.core.LQEntries     = args.cpu_lq
        c.core.SQEntries     = args.cpu_sq
        c.core.fetchWidth    = args.cpu_fetch_width
        c.core.decodeWidth   = args.cpu_fetch_width
        c.core.commitWidth   = args.cpu_fetch_width
        c.core.dispatchWidth = args.cpu_issue_width
        c.core.issueWidth    = args.cpu_issue_width
    print(
        f"[O3MicroarchEvidence] cpu_type=o3 ROB={args.cpu_rob} "
        f"LQ={args.cpu_lq} SQ={args.cpu_sq} "
        f"fetchW={args.cpu_fetch_width} issueW={args.cpu_issue_width}",
        flush=True,
    )

board = SimpleBoard(
    clk_freq=args.cpu_clk_freq,
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)
print(
    f"[ClockEvidence] cpu_clk={args.cpu_clk_freq} "
    f"noc_clk={args.noc_clk_freq}",
    flush=True,
)

binary_resource = BinaryResource(local_path=args.binary)
binary_args = args.args.split() if args.args else []

board.set_se_binary_workload(binary_resource, arguments=binary_args)

print(
    f"Starting CHI benchmark: binary={args.binary} "
    f"cores={args.num_cores} hn_amo_policy={args.hn_amo_policy} "
    f"cpu={args.cpu_type}"
)

simulator = Simulator(board=board)
simulator.run()

print(f"Exiting @ tick {m5.curTick()}")
