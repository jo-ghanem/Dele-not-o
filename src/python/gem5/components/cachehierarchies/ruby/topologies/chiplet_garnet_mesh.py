# Copyright (c) 2026 (Dele-not-o project, chiplet-heterogarnet plan, §5).
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.

"""ChipletGarnetMesh — paper-faithful (topology + latency) HeteroGarnet mesh.

Implements the chiplet.pdf §6.1 / Table 3 network: per-chiplet 2D mesh
(default 6 columns x 4 rows = 24 routers per chiplet) with weighted XY
routing, joined by a single inter-chiplet bridge IntLink of fixed latency
(default 100 cycles = 50 ns @ 2 GHz).

Bandwidth disposition (option C from chiplet-heterogarnet plan §2a):
- Each link is one Garnet flit wide (default ni_flit_size=16 B), giving
  ~32 GB/s per direction. Paper specifies 450 GB/s on the inter-chiplet
  link; this is NOT modeled here. Latency is paper-faithful (50 ns); the
  bandwidth gap is documented in sweep_results/GARNET_TOPOLOGY_EVIDENCE.md
  and labeled in every benchmark summary.

Per-chiplet mesh follows the Mesh_XY pattern:
- East/West links: weight=1 (XY routing priority)
- North/South links: weight=2 (after-X-then-Y to prevent deadlock)
- Controllers attach to per-chiplet routers using divmod distribution.
"""

from m5.objects import (
    GarnetExtLink,
    GarnetIntLink,
    GarnetNetwork,
    GarnetRouter,
)


class ChipletGarnetMesh(GarnetNetwork):
    """HeteroGarnet 2-chiplet 2D-mesh topology with chiplet-aware bridge.

    Same connectControllers(controllers, controller_to_chiplet) signature
    as ChipletPt2Pt so dual_chiplet_private_l1_l2_cache_hierarchy.py can
    swap network class with no other changes. controller_to_chiplet maps
    each controller to its chiplet id (0 or 1 for v1).
    """

    def __init__(
        self,
        ruby_system,
        mesh_rows: int = 4,
        mesh_cols: int = 6,
        intra_link_lat: int = 1,
        inter_link_lat: int = 100,
        bridge_router_idx: int = 0,
        router_latency: int = 1,
    ):
        super().__init__()
        self.netifs = []
        self.ruby_system = ruby_system

        # Garnet itself uses num_rows for some internal accounting; we set it
        # to the per-chiplet mesh row count so each chiplet is one "logical"
        # 2D mesh from Garnet's perspective. The two meshes are joined via a
        # single bridge IntLink in connectControllers.
        self.num_rows = mesh_rows

        self._mesh_rows = mesh_rows
        self._mesh_cols = mesh_cols
        self._intra_link_lat = intra_link_lat
        self._inter_link_lat = inter_link_lat
        self._bridge_router_idx = bridge_router_idx
        self._router_latency = router_latency

    def connectControllers(self, controllers, controller_to_chiplet=None):
        """Build per-chiplet meshes + one inter-chiplet bridge IntLink.

        :param controllers: list of all cache/dir/memctrl/dma controllers.
        :param controller_to_chiplet: dict[controller -> chiplet_id]. Required
            for v1 (must classify every controller); a controller missing
            from the map raises AssertionError.
        """
        assert controller_to_chiplet is not None, (
            "ChipletGarnetMesh.connectControllers requires "
            "controller_to_chiplet (dict mapping controller -> chiplet_id). "
            "See dual_chiplet_private_l1_l2_cache_hierarchy.py for the "
            "expected map construction."
        )
        for c in controllers:
            assert c in controller_to_chiplet, (
                f"Controller {c} is not in controller_to_chiplet map. "
                f"Every controller must be classified for the mesh to wire "
                f"the correct ExtLink to its chiplet's router."
            )

        # 1. Discover chiplet count from the map values.
        chiplet_ids = sorted(set(controller_to_chiplet.values()))
        num_chiplets = len(chiplet_ids)
        assert num_chiplets >= 2, (
            f"ChipletGarnetMesh expected >=2 chiplets, got {num_chiplets}. "
            f"For single-chiplet runs, use SimplePt2Pt instead."
        )

        # 2. Bucket controllers by chiplet.
        per_chiplet_ctrls = {cid: [] for cid in chiplet_ids}
        for c in controllers:
            per_chiplet_ctrls[controller_to_chiplet[c]].append(c)

        # 3. Build per-chiplet routers. Global router index layout:
        #    chiplet 0: routers[0 .. R-1]
        #    chiplet 1: routers[R .. 2R-1]   where R = mesh_rows * mesh_cols
        #    etc.
        R = self._mesh_rows * self._mesh_cols
        all_routers = []
        for cid in chiplet_ids:
            for k in range(R):
                router_id = cid * R + k
                all_routers.append(
                    GarnetRouter(
                        router_id=router_id,
                        latency=self._router_latency,
                    )
                )
        self.routers = all_routers

        # Helper: convert (chiplet_id, router_index_within_chiplet) -> global
        def chip_router(cid, k):
            return self.routers[chiplet_ids.index(cid) * R + k]

        link_count = 0
        ext_links = []
        int_links = []

        # 4. ExtLinks: distribute each chiplet's controllers across its
        # routers using the Mesh_XY divmod pattern.
        for cid in chiplet_ids:
            ctrls = per_chiplet_ctrls[cid]
            num_ctrls = len(ctrls)
            cntrls_per_router, remainder = divmod(num_ctrls, R)
            # First (num_ctrls - remainder) controllers placed evenly;
            # remainder controllers tacked onto router 0 of this chiplet.
            uniform = num_ctrls - remainder
            for i in range(uniform):
                level, router_within = divmod(i, R)
                ext_links.append(
                    GarnetExtLink(
                        link_id=link_count,
                        ext_node=ctrls[i],
                        int_node=chip_router(cid, router_within),
                    )
                )
                link_count += 1
            for j in range(remainder):
                ext_links.append(
                    GarnetExtLink(
                        link_id=link_count,
                        ext_node=ctrls[uniform + j],
                        int_node=chip_router(cid, 0),
                    )
                )
                link_count += 1

        self.ext_links = ext_links

        # 5. Intra-chiplet IntLinks: Mesh_XY pattern (E/W weight=1, N/S weight=2).
        for cid in chiplet_ids:
            mesh_rows = self._mesh_rows
            mesh_cols = self._mesh_cols

            # East -> West (col -> col+1), weight 1
            for row in range(mesh_rows):
                for col in range(mesh_cols):
                    if col + 1 < mesh_cols:
                        east = row * mesh_cols + col
                        west = row * mesh_cols + (col + 1)
                        int_links.append(
                            GarnetIntLink(
                                link_id=link_count,
                                src_node=chip_router(cid, east),
                                dst_node=chip_router(cid, west),
                                src_outport=f"East_chip{cid}",
                                dst_inport=f"West_chip{cid}",
                                latency=self._intra_link_lat,
                                weight=1,
                            )
                        )
                        link_count += 1
                        int_links.append(
                            GarnetIntLink(
                                link_id=link_count,
                                src_node=chip_router(cid, west),
                                dst_node=chip_router(cid, east),
                                src_outport=f"West_chip{cid}",
                                dst_inport=f"East_chip{cid}",
                                latency=self._intra_link_lat,
                                weight=1,
                            )
                        )
                        link_count += 1

            # North -> South (row -> row+1), weight 2
            for col in range(mesh_cols):
                for row in range(mesh_rows):
                    if row + 1 < mesh_rows:
                        north = row * mesh_cols + col
                        south = (row + 1) * mesh_cols + col
                        int_links.append(
                            GarnetIntLink(
                                link_id=link_count,
                                src_node=chip_router(cid, north),
                                dst_node=chip_router(cid, south),
                                src_outport=f"North_chip{cid}",
                                dst_inport=f"South_chip{cid}",
                                latency=self._intra_link_lat,
                                weight=2,
                            )
                        )
                        link_count += 1
                        int_links.append(
                            GarnetIntLink(
                                link_id=link_count,
                                src_node=chip_router(cid, south),
                                dst_node=chip_router(cid, north),
                                src_outport=f"South_chip{cid}",
                                dst_inport=f"North_chip{cid}",
                                latency=self._intra_link_lat,
                                weight=2,
                            )
                        )
                        link_count += 1

        # 6. Inter-chiplet bridge IntLinks: one fat link in each direction
        # between bridge-router on chiplet 0 and bridge-router on chiplet 1.
        # For >2 chiplets (M2 4-chiplet experiment), default to a ring:
        # chiplet i bridges to chiplet (i+1) % num_chiplets. Plan §12.1
        # documents the choice explicitly.
        for i, cid in enumerate(chiplet_ids):
            next_cid = chiplet_ids[(i + 1) % num_chiplets]
            if cid == next_cid:
                continue  # only happens if num_chiplets==1 (already asserted out)
            br_a = chip_router(cid, self._bridge_router_idx)
            br_b = chip_router(next_cid, self._bridge_router_idx)
            int_links.append(
                GarnetIntLink(
                    link_id=link_count,
                    src_node=br_a,
                    dst_node=br_b,
                    src_outport=f"Bridge_chip{cid}_to_chip{next_cid}",
                    dst_inport=f"Bridge_chip{cid}_to_chip{next_cid}",
                    latency=self._inter_link_lat,
                    weight=1,
                )
            )
            link_count += 1
            # Reverse direction is added when the loop visits (next_cid -> cid)
            # for num_chiplets==2; for num_chiplets>=3 the ring direction is
            # uni-directional in this loop, so we explicitly add the reverse:
            if num_chiplets >= 3:
                int_links.append(
                    GarnetIntLink(
                        link_id=link_count,
                        src_node=br_b,
                        dst_node=br_a,
                        src_outport=f"Bridge_chip{next_cid}_to_chip{cid}",
                        dst_inport=f"Bridge_chip{next_cid}_to_chip{cid}",
                        latency=self._inter_link_lat,
                        weight=1,
                    )
                )
                link_count += 1

        # For num_chiplets==2, the loop above adds one bridge (chip0->chip1);
        # add the reverse explicitly so the link is bi-directional.
        if num_chiplets == 2:
            br_a = chip_router(chiplet_ids[1], self._bridge_router_idx)
            br_b = chip_router(chiplet_ids[0], self._bridge_router_idx)
            int_links.append(
                GarnetIntLink(
                    link_id=link_count,
                    src_node=br_a,
                    dst_node=br_b,
                    src_outport=f"Bridge_chip{chiplet_ids[1]}_to_chip{chiplet_ids[0]}",
                    dst_inport=f"Bridge_chip{chiplet_ids[1]}_to_chip{chiplet_ids[0]}",
                    latency=self._inter_link_lat,
                    weight=1,
                )
            )
            link_count += 1

        self.int_links = int_links

        # 7. Topology evidence printouts (Stage A-style; written into
        # GARNET_TOPOLOGY_EVIDENCE.md by the harness on first elaboration).
        print(
            f"[GarnetMeshEvidence] num_chiplets={num_chiplets} "
            f"mesh_rows={self._mesh_rows} mesh_cols={self._mesh_cols} "
            f"routers_per_chiplet={R} total_routers={len(self.routers)} "
            f"ext_links={len(self.ext_links)} int_links={len(self.int_links)} "
            f"intra_link_lat={self._intra_link_lat} "
            f"inter_link_lat={self._inter_link_lat} "
            f"bridge_router_idx={self._bridge_router_idx}",
            flush=True,
        )
        for cid in chiplet_ids:
            ctrl_count = len(per_chiplet_ctrls[cid])
            print(
                f"[GarnetMeshEvidence] chiplet {cid}: {ctrl_count} controllers "
                f"distributed across routers [{cid * R} .. {cid * R + R - 1}]",
                flush=True,
            )
