"""
CHI atomic operation test using MemTest with percent_atomic.
Wraps MemTest in stdlib AbstractGenerator interface so it works
with the stdlib PrivateL1PrivateL2 CHI hierarchy.
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
from gem5.components.processors.abstract_generator import AbstractGenerator
from gem5.components.processors.abstract_generator_core import (
    AbstractGeneratorCore,
)
from gem5.utils.requires import requires

requires(coherence_protocol_required=CoherenceProtocol.CHI)


class MemTestCore(AbstractGeneratorCore):
    def __init__(self, max_loads, percent_atomic):
        super().__init__()
        self.tester = MemTest(
            max_loads=max_loads,
            percent_functional=0,
            percent_uncacheable=0,
            percent_atomic=percent_atomic,
            progress_interval=100000,
            suppress_func_errors=True,
        )

    def connect_dcache(self, port):
        self.tester.port = port

    def start_traffic(self):
        pass


class MemTestGenerator(AbstractGenerator):
    def __init__(self, num_cores, max_loads, percent_atomic):
        super().__init__(
            cores=[
                MemTestCore(max_loads, percent_atomic)
                for _ in range(num_cores)
            ]
        )

    def start_traffic(self):
        pass


parser = argparse.ArgumentParser()
parser.add_argument("--hn-amo-policy", type=int, default=0)
parser.add_argument("--num-cores", type=int, default=4)
parser.add_argument("--max-loads", type=int, default=500)
parser.add_argument("--percent-atomic", type=int, default=50)
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

generator = MemTestGenerator(
    num_cores=args.num_cores,
    max_loads=args.max_loads,
    percent_atomic=args.percent_atomic,
)

board = TestBoard(
    clk_freq="1GHz",
    generator=generator,
    memory=memory,
    cache_hierarchy=cache_hierarchy,
)

root = Root(full_system=False, system=board)
root.system.mem_mode = "timing"

m5.ticks.setGlobalFrequency("1ns")
board._pre_instantiate()
m5.instantiate()

board._post_instantiate()

print(
    f"Starting CHI atomic test: hn_amo_policy={args.hn_amo_policy}, "
    f"percent_atomic={args.percent_atomic}"
)
exit_event = m5.simulate()
print(f"Exiting @ tick {m5.curTick()} because {exit_event.getCause()}")
