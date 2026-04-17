"""
Minimal CHI smoke test using stdlib PrivateL1PrivateL2 hierarchy.
Uses TrafficGen (no binary needed) to verify protocol correctness.
"""

import argparse

import m5
from m5.objects import *

from gem5.coherence_protocol import CoherenceProtocol
from gem5.components.boards.test_board import TestBoard
from gem5.components.cachehierarchies.chi.private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)
from gem5.components.memory import SingleChannelDDR3_1600
from gem5.components.processors.linear_generator import LinearGenerator
from gem5.components.processors.random_generator import RandomGenerator
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

requires(coherence_protocol_required=CoherenceProtocol.CHI)

parser = argparse.ArgumentParser()
parser.add_argument("--hn-amo-policy", type=int, default=0)
parser.add_argument("--num-cores", type=int, default=4)
parser.add_argument("--max-loads", type=int, default=500)
args = parser.parse_args()

cache_hierarchy = PrivateL1PrivateL2CacheHierarchy(
    l1i_size="16KiB",
    l1i_assoc=4,
    l1d_size="16KiB",
    l1d_assoc=4,
    l2_size="64KiB",
    l2_assoc=8,
    hn_amo_policy=args.hn_amo_policy,
    atomic_op_latency=4,
)

memory = SingleChannelDDR3_1600(size="32MiB")

generator = RandomGenerator(
    num_cores=args.num_cores,
    max_addr=0x10000,
    duration="1ms",
    rate="1GB/s",
)

board = TestBoard(
    clk_freq="1GHz",
    generator=generator,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

simulator = Simulator(board=board)
print(f"Starting CHI smoke test with hn_amo_policy={args.hn_amo_policy}")
simulator.run()
print("Test completed successfully!")
