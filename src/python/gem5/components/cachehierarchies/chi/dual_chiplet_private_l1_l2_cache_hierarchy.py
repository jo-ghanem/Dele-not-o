# Copyright (c) 2026 (Dele-not-o project, Stage 2 of wiggly-seeking-swing roadmap).
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.

"""Dual-chiplet PrivateL1+L2 hierarchy.

Extends PrivateL1PrivateL2CacheHierarchy with:
  - 2 HNs (one per chiplet), addr-range interleaved
  - ChipletPt2Pt network with chiplet-aware per-link latency
  - chiplet_id / cores_per_chiplet / num_chiplets passed to every controller
  - elaboration-time hard assertion on (controller version) -> chiplet mapping

Single-edge cross-chiplet latency model (not hop-level). See plan §6 Stage 2.
"""

from itertools import chain
from typing import List

from m5.objects import (
    NULL,
    RubyPortProxy,
    RubySequencer,
    RubySystem,
)
from m5.objects.SubSystem import SubSystem

from gem5.coherence_protocol import CoherenceProtocol
from gem5.utils.requires import requires

requires(coherence_protocol_required=CoherenceProtocol.CHI)

from gem5.components.boards.abstract_board import AbstractBoard
from gem5.components.cachehierarchies.abstract_cache_hierarchy import (
    AbstractCacheHierarchy,
)
from gem5.components.cachehierarchies.ruby.topologies.chiplet_pt2pt import (
    ChipletPt2Pt,
)
from gem5.components.processors.abstract_core import AbstractCore
from gem5.isas import ISA
from gem5.utils.override import overrides

from .nodes.directory import SimpleDirectory
from .nodes.l1_cache import L1CacheController
from .nodes.l2_cache import L2CacheController
from .nodes.memory_controller import MemoryController
from .private_l1_private_l2_cache_hierarchy import (
    PrivateL1PrivateL2CacheHierarchy,
)


class DualChipletPrivateL1PrivateL2CacheHierarchy(
    PrivateL1PrivateL2CacheHierarchy
):
    """Two-chiplet variant of PrivateL1PrivateL2CacheHierarchy.

    Cores 0..(cores_per_chiplet-1) live on chiplet 0; cores
    cores_per_chiplet..(num_chiplets*cores_per_chiplet - 1) on chiplet 1.
    HNs split memory by interleaving on the bit immediately above the cache-line
    offset. Every direct controller-to-controller IntLink crossing chiplet IDs
    gets `inter_link_lat` cycles; intra-chiplet links get `intra_link_lat`.
    """

    def __init__(
        self,
        l1i_size: str,
        l1i_assoc: int,
        l1d_size: str,
        l1d_assoc: int,
        l2_size: str,
        l2_assoc: int,
        hn_amo_policy: int = 0,
        atomic_op_latency: int = 4,
        policy_type: int = 0,
        dynamo_enabled: bool = False,
        dynamo_threshold: int = 1,
        dynamo_variant: int = 0,
        delegato_enabled: bool = False,
        delegato_variant: int = 0,
        num_chiplets: int = 2,
        cores_per_chiplet: int = 16,
        intra_link_lat: int = 1,
        inter_link_lat: int = 100,
    ):
        super().__init__(
            l1i_size=l1i_size,
            l1i_assoc=l1i_assoc,
            l1d_size=l1d_size,
            l1d_assoc=l1d_assoc,
            l2_size=l2_size,
            l2_assoc=l2_assoc,
            hn_amo_policy=hn_amo_policy,
            atomic_op_latency=atomic_op_latency,
            policy_type=policy_type,
            dynamo_enabled=dynamo_enabled,
            dynamo_threshold=dynamo_threshold,
            dynamo_variant=dynamo_variant,
            delegato_enabled=delegato_enabled,
            delegato_variant=delegato_variant,
        )
        self._num_chiplets = num_chiplets
        self._cores_per_chiplet = cores_per_chiplet
        self._intra_link_lat = intra_link_lat
        self._inter_link_lat = inter_link_lat

    @overrides(AbstractCacheHierarchy)
    def incorporate_cache(self, board: AbstractBoard) -> None:
        # Skip parent's incorporate_cache — we replace network + directories.
        # Still call AbstractCacheHierarchy.incorporate_cache via grandparent
        # chain so any base bookkeeping happens.
        super(PrivateL1PrivateL2CacheHierarchy, self).incorporate_cache(board)

        cores = board.get_processor().get_cores()
        num_cores = len(cores)
        expected = self._num_chiplets * self._cores_per_chiplet
        assert num_cores == expected, (
            f"DualChiplet hierarchy expected num_cores == "
            f"num_chiplets * cores_per_chiplet ({expected}); got {num_cores}"
        )

        self.ruby_system = RubySystem()
        self.ruby_system.network = ChipletPt2Pt(
            self.ruby_system,
            intra_link_lat=self._intra_link_lat,
            inter_link_lat=self._inter_link_lat,
        )
        self.ruby_system.number_of_virtual_networks = 4
        self.ruby_system.network.number_of_virtual_networks = 4

        # Per-chiplet HNs with addr-range interleaving.
        mem_ranges = board.get_mem_ports()  # list of (range, port)
        # Use the first range as the source for splitting.
        mem_addr_ranges = [rng for rng, _ in mem_ranges]
        self.directories = []
        for chiplet_idx in range(self._num_chiplets):
            addr_ranges = SimpleDirectory.create_addr_ranges(
                num_directories=self._num_chiplets,
                dir_idx=chiplet_idx,
                mem_ranges=mem_addr_ranges,
                cache_line_size=board.get_cache_line_size(),
            )
            hn = SimpleDirectory(
                self.ruby_system.network,
                cache_line_size=board.get_cache_line_size(),
                clk_domain=board.get_clock_domain(),
                addr_ranges=addr_ranges,
                hn_amo_policy=self._hn_amo_policy,
                delegato_enabled=self._delegato_enabled,
                delegato_variant=self._delegato_variant,
                chiplet_id=chiplet_idx,
                cores_per_chiplet=self._cores_per_chiplet,
                num_chiplets=self._num_chiplets,
            )
            hn.ruby_system = self.ruby_system
            self.directories.append(hn)

        # Core clusters; tag each one with its chiplet_id.
        self.core_clusters = [
            self._create_core_cluster(core, i, board)
            for i, core in enumerate(cores)
        ]

        # Memory controllers (off-chiplet in reality; modeled as chiplet 0
        # because every chiplet still routes through the same memory side).
        self.memory_controllers = self._create_memory_controllers(board)
        for hn in self.directories:
            hn.downstream_destinations = self.memory_controllers

        if board.has_dma_ports():
            # Inherited _create_dma_controllers (parent line ~336) reads
            # self.directory (singular) when wiring downstream_destinations.
            # Alias the first HN so the inherited method doesn't AttributeError;
            # we overwrite downstream_destinations to the full HN list right after.
            self.directory = self.directories[0]
            self.dma_controllers = self._create_dma_controllers(board)
            self.ruby_system.num_of_sequencers = (
                len(self.core_clusters) * 2 + len(self.dma_controllers)
            )
            for ctrl in self.dma_controllers:
                ctrl.downstream_destinations = list(self.directories)
        else:
            self.ruby_system.num_of_sequencers = len(self.core_clusters) * 2

        # Build controller_to_chiplet map.
        controller_to_chiplet = {}
        for i, cluster in enumerate(self.core_clusters):
            chiplet = i // self._cores_per_chiplet
            controller_to_chiplet[cluster.dcache] = chiplet
            controller_to_chiplet[cluster.icache] = chiplet
            controller_to_chiplet[cluster.l2] = chiplet
        for i, hn in enumerate(self.directories):
            controller_to_chiplet[hn] = i
        for mc in self.memory_controllers:
            controller_to_chiplet[mc] = 0
        if board.has_dma_ports():
            for dma in self.dma_controllers:
                controller_to_chiplet[dma] = 0

        # Hard-assert: L1D/L1I/L2 of cluster i should be on chiplet (i // cpc);
        # HN i should be on chiplet i. Catches MachineID-ordering surprises
        # before they corrupt CA-decision results.
        self._assert_chiplet_mapping()

        all_ctrls = (
            list(
                chain.from_iterable(
                    (cluster.dcache, cluster.icache, cluster.l2)
                    for cluster in self.core_clusters
                )
            )
            + self.memory_controllers
            + list(self.directories)
            + (self.dma_controllers if board.has_dma_ports() else [])
        )
        self.ruby_system.network.connectControllers(
            all_ctrls, controller_to_chiplet=controller_to_chiplet
        )
        self.ruby_system.network.setup_buffers()

        self.ruby_system.sys_port_proxy = RubyPortProxy(
            ruby_system=self.ruby_system
        )
        board.connect_system_port(self.ruby_system.sys_port_proxy.in_ports)

    def _assert_chiplet_mapping(self):
        """Two assertions. The first is on the Python-side `chiplet_id` field
        (always passes if our map construction logic is right). The second is
        the load-bearing one: it checks whether the SLICC arithmetic
        `chipletOf(id) := id.num / cores_per_chiplet` would recover the
        correct chiplet for each controller. If the second assertion fails,
        the SLICC chipletOf helper is producing wrong chiplet attributions
        for *remote* MachineIDs (Table 2's req-locality and owner-locality
        will be wrong), and the documented fix is to replace chipletOf with
        a C++ extern lookup table keyed by MachineID, populated from the
        Python controller_to_chiplet map. See plan §8 D4.

        Known issue: in CHI, all of (L1D, L1I, L2, HN) share MachineType:Cache
        and AbstractNode._version is a single global counter incremented by
        AbstractNode.versionCount() (abstract_node.py:75-77). With 32 cores +
        2 HNs the version sequence is HN0=0, HN1=1, c0.d=2, c0.i=3, c0.l2=4,
        c1.d=5, ... — so id.num // cores_per_chiplet does NOT match the true
        chiplet index for most controllers. The C++ extern lookup table is
        the correct fix; the second assertion below will fail loudly so the
        next implementer knows to do that work before any chiplet sweep run.
        """
        # First assertion: Python-side chiplet_id is consistent.
        for i, cluster in enumerate(self.core_clusters):
            expected = i // self._cores_per_chiplet
            for tag, ctrl in (
                ("dcache", cluster.dcache),
                ("icache", cluster.icache),
                ("l2", cluster.l2),
            ):
                assert ctrl.chiplet_id == expected, (
                    f"chiplet mapping (Python) mismatch: "
                    f"cluster[{i}].{tag}.chiplet_id={ctrl.chiplet_id}, "
                    f"expected {expected}"
                )
        for i, hn in enumerate(self.directories):
            assert hn.chiplet_id == i, (
                f"chiplet mapping (Python) mismatch: "
                f"directories[{i}].chiplet_id={hn.chiplet_id}, expected {i}"
            )

        # Second assertion: SLICC formula chipletOf matches the Python
        # controller_to_chiplet map. The SLICC formula is:
        #   if id.num < num_chiplets: return id.num
        #   else: return (id.num - num_chiplets) / (cores_per_chiplet * 3)
        # This is fragile (depends on the controller-creation order in
        # incorporate_cache); if it fails, replace chipletOf body with a C++
        # extern keyed by MachineID (plan §8 D4 / Stage 2.5).
        cpc = self._cores_per_chiplet
        nc = self._num_chiplets

        def slicc_chiplet_of(version):
            if version < nc:
                return version
            return (version - nc) // (cpc * 3)

        slicc_failures = []
        for i, cluster in enumerate(self.core_clusters):
            expected = i // cpc
            for tag, ctrl in (
                ("dcache", cluster.dcache),
                ("icache", cluster.icache),
                ("l2", cluster.l2),
            ):
                got = slicc_chiplet_of(ctrl.version)
                if got != expected:
                    slicc_failures.append(
                        f"cluster[{i}].{tag}: version={ctrl.version}, "
                        f"slicc_chipletOf={got}, expected={expected}"
                    )
        for i, hn in enumerate(self.directories):
            got = slicc_chiplet_of(hn.version)
            if got != i:
                slicc_failures.append(
                    f"directories[{i}]: version={hn.version}, "
                    f"slicc_chipletOf={got}, expected={i}"
                )
        if slicc_failures:
            msg = (
                "SLICC chipletOf formula does NOT recover the correct chiplet "
                "for every controller. Table 2 chiplet-aware logic in "
                "CHI-cache-actions.sm would produce WRONG results. Either "
                "fix the controller-creation order in incorporate_cache OR "
                "replace chipletOf body with a C++ extern lookup table keyed "
                "by MachineID (plan §8 D4 / Stage 2.5). Failures:\n  "
                + "\n  ".join(slicc_failures[:8])
                + (f"\n  ... ({len(slicc_failures)} total)"
                   if len(slicc_failures) > 8 else "")
            )
            raise AssertionError(msg)

    def _create_core_cluster(
        self, core: AbstractCore, core_num: int, board: AbstractBoard
    ) -> SubSystem:
        """Identical to parent except: assigns chiplet_id, cores_per_chiplet,
        num_chiplets to dcache/icache/l2; downstream_destinations now point
        to the list of HNs (so requests get routed to the correct HN by the
        Ruby addr_ranges)."""
        cluster = SubSystem()
        chiplet = core_num // self._cores_per_chiplet

        cluster.dcache = L1CacheController(
            size=self._l1d_size,
            assoc=self._l1d_assoc,
            network=self.ruby_system.network,
            requires_send_evicts=core.requires_send_evicts(),
            cache_line_size=board.get_cache_line_size(),
            target_isa=board.get_processor().get_isa(),
            clk_domain=board.get_clock_domain(),
            chiplet_id=chiplet,
            cores_per_chiplet=self._cores_per_chiplet,
            num_chiplets=self._num_chiplets,
        )
        cluster.dcache.policy_type = self._policy_type
        cluster.dcache.hn_amo_policy = self._hn_amo_policy
        cluster.dcache.dynamo_enabled = self._dynamo_enabled
        cluster.dcache.dynamo_threshold = self._dynamo_threshold
        cluster.dcache.dynamo_variant = self._dynamo_variant
        cluster.dcache.delegato_enabled = self._delegato_enabled
        cluster.dcache.delegato_variant = self._delegato_variant

        cluster.icache = L1CacheController(
            size=self._l1i_size,
            assoc=self._l1i_assoc,
            network=self.ruby_system.network,
            requires_send_evicts=core.requires_send_evicts(),
            cache_line_size=board.get_cache_line_size(),
            target_isa=board.get_processor().get_isa(),
            clk_domain=board.get_clock_domain(),
            chiplet_id=chiplet,
            cores_per_chiplet=self._cores_per_chiplet,
            num_chiplets=self._num_chiplets,
        )

        cluster.icache.sequencer = RubySequencer(
            version=core_num,
            dcache=NULL,
            clk_domain=cluster.icache.clk_domain,
            ruby_system=self.ruby_system,
        )
        cluster.dcache.sequencer = RubySequencer(
            version=core_num,
            dcache=cluster.dcache.cache,
            clk_domain=cluster.dcache.clk_domain,
            ruby_system=self.ruby_system,
        )

        cluster.icache.hn_amo_policy = self._hn_amo_policy
        cluster.icache.delegato_enabled = self._delegato_enabled
        cluster.icache.delegato_variant = self._delegato_variant

        cluster.l2 = L2CacheController(
            size=self._l2_size,
            assoc=self._l2_assoc,
            network=self.ruby_system.network,
            cache_line_size=board.get_cache_line_size(),
            clk_domain=board.get_clock_domain(),
            atomic_op_latency=self._atomic_op_latency,
            chiplet_id=chiplet,
            cores_per_chiplet=self._cores_per_chiplet,
            num_chiplets=self._num_chiplets,
        )
        cluster.l2.hn_amo_policy = self._hn_amo_policy
        cluster.l2.delegato_enabled = self._delegato_enabled
        cluster.l2.delegato_variant = self._delegato_variant

        if board.has_io_bus():
            cluster.dcache.sequencer.connectIOPorts(board.get_io_bus())

        cluster.dcache.ruby_system = self.ruby_system
        cluster.icache.ruby_system = self.ruby_system
        cluster.l2.ruby_system = self.ruby_system

        core.connect_icache(cluster.icache.sequencer.in_ports)
        core.connect_dcache(cluster.dcache.sequencer.in_ports)

        core.connect_walker_ports(
            cluster.dcache.sequencer.in_ports,
            cluster.icache.sequencer.in_ports,
        )

        if board.get_processor().get_isa() == ISA.X86:
            int_req_port = cluster.dcache.sequencer.interrupt_out_port
            int_resp_port = cluster.dcache.sequencer.in_ports
            core.connect_interrupt(int_req_port, int_resp_port)
        else:
            core.connect_interrupt()

        cluster.dcache.downstream_destinations = [cluster.l2]
        cluster.icache.downstream_destinations = [cluster.l2]
        cluster.l2.downstream_destinations = list(self.directories)

        return cluster
