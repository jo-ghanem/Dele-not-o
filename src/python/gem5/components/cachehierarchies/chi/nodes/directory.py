# Copyright (c) 2021-2025 Arm Limited
# All rights reserved.
#
# The license below extends only to copyright in the software and shall
# not be construed as granting a license to any other intellectual
# property including but not limited to intellectual property relating
# to a hardware implementation of the functionality of the software
# licensed hereunder.  You may use the software subject to the license
# terms below provided that you ensure that this notice is replicated
# unmodified and in its entirety in all distributions of the software,
# modified or unmodified, in source code or in binary form.
#
# Copyright (c) 2021 The Regents of the University of California
# All Rights Reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import math
from typing import List

from m5.objects import (
    ClockDomain,
    RubyCache,
    RubyNetwork,
)
from m5.params import (
    NULL,
    AddrRange,
)

from .abstract_node import AbstractNode


class BaseDirectory(AbstractNode):
    """
    BaseDirectory. Mainly providing address range generation
    capabilities (see create_addr_ranges method)
    """

    def __init__(
        self,
        network: RubyNetwork,
        cache_line_size: int,
    ):
        super().__init__(network, cache_line_size)

    @classmethod
    def create_addr_ranges(
        cls,
        num_directories: int,
        dir_idx: int,
        mem_ranges: List[AddrRange],
        cache_line_size,
        numa_id: int = 0,
        num_numa_domains: int = 1,
    ) -> List[AddrRange]:
        """Create per-HN address ranges with optional NUMA partition.

        Single-level (num_numa_domains=1, legacy): addresses interleave
        across `num_directories` HNs by cache-line-offset bits across the
        whole memory.

        Two-level (num_numa_domains>1, chiplet S1 — Grace-CPU NUMA model
        per chiplet.pdf §6.1 + author errata): memory is first split into
        `num_numa_domains` contiguous halves; only HNs in NUMA half k cache
        addresses in the kth half. `num_directories` becomes the within-NUMA
        HN count (= hns_per_chiplet); `dir_idx` is the HN index within the
        NUMA half (0..hns_per_chiplet-1). The two-level partition is
        achieved by combining the AddrRange's start/size (top bits select
        NUMA half) with intlvHighBit/intlvBits/intlvMatch (low bits select
        within-NUMA HN).
        """
        assert num_numa_domains >= 1 and (
            num_numa_domains & (num_numa_domains - 1) == 0
        ), (
            f"num_numa_domains must be a power of 2; got {num_numa_domains}"
        )
        assert 0 <= numa_id < num_numa_domains, (
            f"numa_id {numa_id} out of range for num_numa_domains "
            f"{num_numa_domains}"
        )

        block_size_bits = int(math.log(cache_line_size, 2))
        llc_bits = int(math.log(num_directories, 2))
        intlv_high_bit = block_size_bits + llc_bits - 1

        ranges = []
        for r in mem_ranges:
            assert r.size() % num_numa_domains == 0, (
                f"mem_range size {r.size()} not divisible by "
                f"num_numa_domains {num_numa_domains}"
            )
            numa_size = r.size() // num_numa_domains
            numa_start = int(r.start) + numa_id * numa_size
            addr_range = AddrRange(
                numa_start,
                size=numa_size,
                intlvHighBit=intlv_high_bit,
                intlvBits=llc_bits,
                intlvMatch=dir_idx,
            )
            ranges.append(addr_range)
        return ranges


class SimpleDirectory(BaseDirectory):
    """A directory or home node (HNF)

    This simple directory has no cache. It forwards all requests as directly
    as possible.
    """

    def __init__(
        self,
        network: RubyNetwork,
        cache_line_size: int,
        clk_domain: ClockDomain,
        addr_ranges: List[AddrRange],
        hn_amo_policy: int = 0,
        delegato_enabled: bool = False,
        delegato_variant: int = 0,
        chiplet_id: int = 0,
        cores_per_chiplet: int = 16,
        num_chiplets: int = 1,
    ):
        super().__init__(network, cache_line_size)

        # Dummy cache
        self.cache = RubyCache(
            dataAccessLatency=0, tagAccessLatency=1, size="128", assoc=1
        )

        self.addr_ranges = addr_ranges
        self.clk_domain = clk_domain

        # Only used for L1 controllers
        self.send_evictions = False
        self.sequencer = NULL

        self.use_prefetcher = False
        self.prefetcher = NULL

        # Set up home node that allows three hop protocols
        self.is_HN = True
        self.enable_DMT = True
        self.enable_DCT = True

        # Delegated AMO policy: 0=All-Central, 1=Pinned-Owner, 2=Unowned-Central, 3=All-Migrate, 4=Delegato
        self.hn_amo_policy = hn_amo_policy
        self.delegato_enabled = delegato_enabled
        self.delegato_variant = delegato_variant

        # Chiplet topology params (Stage 2 — wiggly-seeking-swing roadmap)
        self.chiplet_id = chiplet_id
        self.cores_per_chiplet = cores_per_chiplet
        self.num_chiplets = num_chiplets

        # "Owned state"
        self.allow_SD = True

        # No cache
        self.alloc_on_seq_acc = False
        self.alloc_on_seq_line_write = False
        self.alloc_on_readshared = False
        self.alloc_on_readunique = False
        self.alloc_on_readonce = False
        self.alloc_on_writeback = False
        self.alloc_on_atomic = False
        self.dealloc_on_unique = False
        self.dealloc_on_shared = False
        self.dealloc_backinv_unique = False
        self.dealloc_backinv_shared = False

        # Some reasonable default TBE params
        self.number_of_TBEs = 32
        self.number_of_repl_TBEs = 32
        self.number_of_snoop_TBEs = 1
        self.number_of_DVM_TBEs = 1  # should not receive any dvm
        self.number_of_DVM_snoop_TBEs = 1  # should not receive any dvm
        self.unify_repl_TBEs = False
