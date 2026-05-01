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
    GarnetNetworkInterface,
    GarnetRouter,
    NetworkBridge,
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
        mesh_cols: int = 4,
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

    def connectControllers(
        self,
        controllers,
        controller_to_chiplet=None,
        controller_to_router_idx=None,
    ):
        """Build per-chiplet meshes + one inter-chiplet bridge IntLink.

        :param controllers: list of all cache/dir/memctrl/dma controllers.
        :param controller_to_chiplet: dict[controller -> chiplet_id]. Required;
            a controller missing from the map raises AssertionError.
        :param controller_to_router_idx: optional dict[controller ->
            within-chiplet router index]. When provided, every controller is
            attached to the specified router index (0..R-1) of its chiplet.
            Used by S5 tile-coupling: a tile's L1i+L1d+L2+HN are co-located
            on a single router. When None, falls back to the legacy Mesh_XY
            divmod distribution (used when cores_per_chiplet !=
            hns_per_chiplet, e.g. M2 4-chiplet × 8-cpc).
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
        if controller_to_router_idx is not None:
            for c in controllers:
                assert c in controller_to_router_idx, (
                    f"Controller {c} is not in controller_to_router_idx map. "
                    f"With tile coupling enabled, every controller needs a "
                    f"within-chiplet router index."
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

        # 4. ExtLinks. Tile-coupling path (S5) when controller_to_router_idx
        # is provided: each controller attaches to its mapped router; legacy
        # divmod fallback otherwise.
        if controller_to_router_idx is not None:
            for cid in chiplet_ids:
                for c in per_chiplet_ctrls[cid]:
                    r_idx = controller_to_router_idx[c]
                    assert 0 <= r_idx < R, (
                        f"controller_to_router_idx[{c}] = {r_idx} out of "
                        f"range [0, {R}) for mesh_rows*mesh_cols={R}"
                    )
                    ext_links.append(
                        GarnetExtLink(
                            link_id=link_count,
                            ext_node=c,
                            int_node=chip_router(cid, r_idx),
                        )
                    )
                    link_count += 1
        else:
            # Legacy Mesh_XY divmod distribution
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

        # 6. S9: Inter-chiplet bridge IntLinks at the bisection.
        # Per author A4 confirmation, two 4×4 chiplet meshes are joined at
        # their column-boundary by ONE bridge per row — i.e., for the
        # 2-chiplet case: mesh_rows links each connecting (row r, last col
        # of chip 0) to (row r, first col of chip 1), bidirectional.
        # For num_chiplets > 2, fall back to the legacy single-bridge ring
        # (M2 layout). Each bridge gets latency=inter_link_lat (paper 50 ns
        # @ 2 GHz NoC = 100 cyc).
        #
        # Total inter-chiplet IntLinks for 2 chiplets:
        #   2 directions × mesh_rows rows = 2*mesh_rows.
        # Each row's pair (src_outport, dst_inport) is uniquely named so
        # Topology.cc:165 ("Two links connecting same src and destination
        # cannot support same vnets") cannot trigger.
        if num_chiplets == 2:
            a, b = chiplet_ids[0], chiplet_ids[1]
            for r in range(self._mesh_rows):
                a_router_idx = r * self._mesh_cols + (self._mesh_cols - 1)
                b_router_idx = r * self._mesh_cols + 0
                br_a = chip_router(a, a_router_idx)
                br_b = chip_router(b, b_router_idx)
                int_links.append(
                    GarnetIntLink(
                        link_id=link_count,
                        src_node=br_a,
                        dst_node=br_b,
                        src_outport=f"Bisect_chip{a}_row{r}",
                        dst_inport=f"Bisect_chip{a}_row{r}",
                        latency=self._inter_link_lat,
                        weight=1,
                    )
                )
                link_count += 1
                int_links.append(
                    GarnetIntLink(
                        link_id=link_count,
                        src_node=br_b,
                        dst_node=br_a,
                        src_outport=f"Bisect_chip{b}_row{r}",
                        dst_inport=f"Bisect_chip{b}_row{r}",
                        latency=self._inter_link_lat,
                        weight=1,
                    )
                )
                link_count += 1
        else:
            # Legacy ring topology for >2 chiplets (M2 4-chiplet layout).
            # One bridge per neighboring chiplet pair via bridge_router_idx.
            bridge_pairs = set()
            for i in range(num_chiplets):
                a = chiplet_ids[i]
                b = chiplet_ids[(i + 1) % num_chiplets]
                if a == b:
                    continue
                pair = tuple(sorted([a, b]))
                if pair in bridge_pairs:
                    continue
                bridge_pairs.add(pair)
                br_a = chip_router(a, self._bridge_router_idx)
                br_b = chip_router(b, self._bridge_router_idx)
                int_links.append(
                    GarnetIntLink(
                        link_id=link_count,
                        src_node=br_a,
                        dst_node=br_b,
                        src_outport=f"Bridge_chip{a}_to_chip{b}",
                        dst_inport=f"Bridge_chip{a}_to_chip{b}",
                        latency=self._inter_link_lat,
                        weight=1,
                    )
                )
                link_count += 1
                int_links.append(
                    GarnetIntLink(
                        link_id=link_count,
                        src_node=br_b,
                        dst_node=br_a,
                        src_outport=f"Bridge_chip{b}_to_chip{a}",
                        dst_inport=f"Bridge_chip{b}_to_chip{a}",
                        latency=self._inter_link_lat,
                        weight=1,
                    )
                )
                link_count += 1

        self.int_links = int_links

        # 7. NetworkInterface per ExtLink + NetworkBridge wiring on every link.
        # Required for Garnet init() — without these the C++ side segfaults in
        # GarnetNetwork::init(). Pattern lifted from configs/network/Network.py
        # (legacy Garnet flow, lines 168-279). Bridges handle CDC + SerDes for
        # heterogeneous-width networks (HeteroGarnet feature); for our v1 the
        # widths are uniform (default ni_flit_size=16 B everywhere) so the
        # bridges are pass-through but still mandatory.
        self.netifs = [
            GarnetNetworkInterface(id=i)
            for i in range(len(self.ext_links))
        ]

        for il in self.int_links:
            il.src_net_bridge = NetworkBridge(
                link=il.network_link,
                vtype="OBJECT_LINK",
                width=il.src_node.width,
            )
            il.src_cred_bridge = NetworkBridge(
                link=il.credit_link,
                vtype="LINK_OBJECT",
                width=il.src_node.width,
            )
            il.dst_net_bridge = NetworkBridge(
                link=il.network_link,
                vtype="LINK_OBJECT",
                width=il.dst_node.width,
            )
            il.dst_cred_bridge = NetworkBridge(
                link=il.credit_link,
                vtype="OBJECT_LINK",
                width=il.dst_node.width,
            )

        for el in self.ext_links:
            el.ext_net_bridge = [
                NetworkBridge(
                    link=el.network_links[0],
                    vtype="OBJECT_LINK",
                    width=el.width,
                ),
                NetworkBridge(
                    link=el.network_links[1],
                    vtype="LINK_OBJECT",
                    width=el.width,
                ),
            ]
            el.ext_cred_bridge = [
                NetworkBridge(
                    link=el.credit_links[0],
                    vtype="LINK_OBJECT",
                    width=el.width,
                ),
                NetworkBridge(
                    link=el.credit_links[1],
                    vtype="OBJECT_LINK",
                    width=el.width,
                ),
            ]
            el.int_net_bridge = [
                NetworkBridge(
                    link=el.network_links[0],
                    vtype="LINK_OBJECT",
                    width=el.int_node.width,
                ),
                NetworkBridge(
                    link=el.network_links[1],
                    vtype="OBJECT_LINK",
                    width=el.int_node.width,
                ),
            ]
            el.int_cred_bridge = [
                NetworkBridge(
                    link=el.credit_links[0],
                    vtype="OBJECT_LINK",
                    width=el.int_node.width,
                ),
                NetworkBridge(
                    link=el.credit_links[1],
                    vtype="LINK_OBJECT",
                    width=el.int_node.width,
                ),
            ]

        # 8. Topology evidence printouts (Stage A-style; written into
        # GARNET_TOPOLOGY_EVIDENCE.md by the harness on first elaboration).
        if num_chiplets == 2:
            bridge_topology = (
                f"bisection ({self._mesh_rows} pairs × 2 dir = "
                f"{2 * self._mesh_rows} IntLinks)"
            )
        else:
            bridge_topology = "ring (1 pair per neighbor × 2 dir)"
        print(
            f"[GarnetMeshEvidence] num_chiplets={num_chiplets} "
            f"mesh_rows={self._mesh_rows} mesh_cols={self._mesh_cols} "
            f"routers_per_chiplet={R} total_routers={len(self.routers)} "
            f"ext_links={len(self.ext_links)} int_links={len(self.int_links)} "
            f"intra_link_lat={self._intra_link_lat} "
            f"inter_link_lat={self._inter_link_lat} "
            f"bridge_topology={bridge_topology}",
            flush=True,
        )
        if num_chiplets == 2:
            for r in range(self._mesh_rows):
                a_idx = r * self._mesh_cols + (self._mesh_cols - 1)
                b_idx = r * self._mesh_cols + 0
                a_router = chiplet_ids[0] * R + a_idx
                b_router = chiplet_ids[1] * R + b_idx
                print(
                    f"[GarnetMeshEvidence] bisection row={r}: "
                    f"router{a_router} <-> router{b_router} "
                    f"latency={self._inter_link_lat}cy",
                    flush=True,
                )
        for cid in chiplet_ids:
            ctrl_count = len(per_chiplet_ctrls[cid])
            mode = "tile-coupled" if controller_to_router_idx else "divmod"
            print(
                f"[GarnetMeshEvidence] chiplet {cid}: {ctrl_count} controllers "
                f"distributed across routers [{cid * R} .. {cid * R + R - 1}] "
                f"({mode})",
                flush=True,
            )
        # S5: per-router tile summary when tile coupling is active
        if controller_to_router_idx is not None:
            for cid in chiplet_ids:
                ctrls_by_router = {}
                for c in per_chiplet_ctrls[cid]:
                    r = controller_to_router_idx[c]
                    ctrls_by_router.setdefault(r, []).append(c)
                for r in sorted(ctrls_by_router.keys()):
                    cs = ctrls_by_router[r]
                    types = ",".join(
                        type(c).__name__.replace("Controller", "")
                        for c in cs
                    )
                    print(
                        f"[GarnetMeshEvidence] tile chiplet={cid} router={r}: "
                        f"{len(cs)} ctrls [{types}]",
                        flush=True,
                    )

    def setup_buffers(self):
        """No-op for Garnet networks.

        SimpleNetwork hierarchies require per-link buffer setup
        (SimpleNetwork.py:73-78); Garnet routers and links manage their
        buffers internally via VC counts (vcs_per_vnet, buffers_per_data_vc,
        buffers_per_ctrl_vc on GarnetNetwork). The stdlib hierarchy calls
        setup_buffers() unconditionally after connectControllers(), so we
        provide this no-op to keep that call site working without a network-
        type branch in the hierarchy.
        """
        pass
