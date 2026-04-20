"""
CHI benchmark runner for SE mode.
Runs any statically-linked X86 binary under the CHI PrivateL1PrivateL2
cache hierarchy with configurable hn_amo_policy for migrating/delegated
AMO testing.

Usage:
    build/X86/gem5.opt configs/example/chi_benchmark_se_x86.py \
        --binary /path/to/x86_static_binary \
        --args "-p 1 -m 16" \
        --num-cores 1 \
        --hn-amo-policy 3
"""

import argparse

import m5
from m5.objects import *

from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.simple_board import SimpleBoard
from gem5.components.cachehierarchies.chi.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.memory import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import BinaryResource
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

requires(
    isa_required=ISA.X86,
    coherence_protocol_required=CoherenceProtocol.CHI,
)

parser = argparse.ArgumentParser(
    description="Run an X86 SE binary with CHI cache hierarchy"
)
parser.add_argument(
    "--binary",
    type=str,
    required=True,
    help="Path to statically-linked X86 binary",
)
parser.add_argument(
    "--args",
    type=str,
    default="",
    help="Arguments to pass to the binary (space-separated string)",
)
parser.add_argument("--num-cores", type=int, default=4)
parser.add_argument(
    "--hn-amo-policy",
    type=int,
    default=0,
    help="0=All-Central, 1=Pinned-Owner, 2=Unowned-Central, 3=All-Migrate",
)
parser.add_argument(
    "--cpu-type",
    type=str,
    default="timing",
    choices=["timing", "atomic", "o3"],
    help="CPU type (timing recommended for protocol testing)",
)
parser.add_argument("--l1d-size", type=str, default="32KiB")
parser.add_argument("--l2-size", type=str, default="256KiB")
parser.add_argument("--mem-size", type=str, default="512MiB")
args = parser.parse_args()

cpu_type_map = {
    "timing": CPUTypes.TIMING,
    "atomic": CPUTypes.ATOMIC,
    "o3": CPUTypes.O3,
}

cache_hierarchy = PrivateL1PrivateL2CacheHierarchy(
    l1i_size="32KiB",
    l1i_assoc=4,
    l1d_size=args.l1d_size,
    l1d_assoc=4,
    l2_size=args.l2_size,
    l2_assoc=8,
    hn_amo_policy=args.hn_amo_policy,
    atomic_op_latency=4,
    policy_type=1,
)

memory = SingleChannelDDR3_1600(size=args.mem_size)

processor = SimpleProcessor(
    cpu_type=cpu_type_map[args.cpu_type],
    isa=ISA.X86,
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