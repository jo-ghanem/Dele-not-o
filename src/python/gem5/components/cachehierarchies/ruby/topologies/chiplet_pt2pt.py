# Copyright (c) 2026 (Dele-not-o project, Stage 2 of wiggly-seeking-swing roadmap).
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.

from m5.objects import (
    SimpleExtLink,
    SimpleIntLink,
    SimpleNetwork,
    Switch,
)


class ChipletPt2Pt(SimpleNetwork):
    """Chiplet-aware point-to-point network.

    Same per-controller-router full mesh as SimplePt2Pt, but each internal link
    receives a latency that depends on whether its src/dst controllers belong
    to the same chiplet.

    This is a single-edge approximation, NOT a hop-level NoC model. Paper-faithful
    6x4 mesh fidelity is Stage 2b's job (configs/topologies/ChipletMesh.py).
    """

    def __init__(self, ruby_system, intra_link_lat=1, inter_link_lat=100):
        super().__init__()
        self.netifs = []
        self.ruby_system = ruby_system
        self._intra_link_lat = intra_link_lat
        self._inter_link_lat = inter_link_lat

    def connectControllers(self, controllers, controller_to_chiplet=None):
        """Connect controllers via per-controller routers; set IntLink latency
        from chiplet-membership lookup of src/dst controllers.

        :param controllers: list of cache/dir/memctrl/dma controllers
        :param controller_to_chiplet: dict mapping controller object -> chiplet_id (int).
            If None or a controller is missing from the map, treats it as chiplet 0
            (degrades gracefully to the SimplePt2Pt single-chiplet behavior).
        """
        if controller_to_chiplet is None:
            controller_to_chiplet = {}

        self.routers = [Switch(router_id=i) for i in range(len(controllers))]

        self.ext_links = [
            SimpleExtLink(link_id=i, ext_node=c, int_node=self.routers[i])
            for i, c in enumerate(controllers)
        ]

        # Per-router chiplet, indexed identically to routers/controllers.
        router_chiplet = [
            controller_to_chiplet.get(c, 0) for c in controllers
        ]

        link_count = 0
        int_links = []
        for i, ri in enumerate(self.routers):
            for j, rj in enumerate(self.routers):
                if ri == rj:
                    continue
                link_count += 1
                lat = (
                    self._intra_link_lat
                    if router_chiplet[i] == router_chiplet[j]
                    else self._inter_link_lat
                )
                int_links.append(
                    SimpleIntLink(
                        link_id=link_count,
                        src_node=ri,
                        dst_node=rj,
                        latency=lat,
                    )
                )
        self.int_links = int_links
