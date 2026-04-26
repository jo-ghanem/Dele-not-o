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
parser.add_argument("--cpu-type", type=str, default="timing",
    choices=["timing", "atomic", "o3"],
    help="CPU type (timing recommended for protocol testing)")
parser.add_argument("--l1d-size", type=str, default="32KiB")
parser.add_argument("--l2-size", type=str, default="256KiB")
parser.add_argument("--mem-size", type=str, default="512MiB")
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
parser.add_argument("--mesh-cols", type=int, default=6,
    help="(chiplet+garnet only) per-chiplet mesh column count (default 6)")
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
args = parser.parse_args()

cpu_type_map = {
    "timing": CPUTypes.TIMING,
    "atomic": CPUTypes.ATOMIC,
    "o3": CPUTypes.O3,
}

if args.topology == "chiplet":
    cache_hierarchy = DualChipletPrivateL1PrivateL2CacheHierarchy(
        l1i_size="32KiB",
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
    )
else:
    cache_hierarchy = PrivateL1PrivateL2CacheHierarchy(
        l1i_size="32KiB",
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

memory = SingleChannelDDR3_1600(size=args.mem_size)

processor = SimpleProcessor(
    cpu_type=cpu_type_map[args.cpu_type],
    isa=ISA.ARM,
    num_cores=args.num_cores,
)

board = SimpleBoard(
    clk_freq="2GHz",
    processor=processor,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
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
