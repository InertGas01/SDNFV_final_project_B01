from __future__ import annotations

from dataclasses import dataclass

from src.topo.fattree import FatTreeTopology


@dataclass(frozen=True)
class Rule:
    switch: str
    role: str
    in_port: int | None
    eth_src: str
    eth_dst: str
    match_vlan: int | None
    out_port: int
    set_vlan: int | None
    pop_vlan: bool
    cookie: int

    def match_key(self) -> tuple[int | None, str, str, int | None]:
        return (self.in_port, self.eth_src, self.eth_dst, self.match_vlan)


Config = dict[str, frozenset[Rule]]


def build_config(
    topo: FatTreeTopology,
    routes: dict[tuple[str, str], list[str]],
    version: int,
) -> Config:
    rules_by_sw: dict[str, list[Rule]] = {sw: [] for sw in topo.switches}

    for (src, dst), path in routes.items():
        src_mac = topo.host_mac[src]
        dst_mac = topo.host_mac[dst]

        if len(path) == 1:
            sw = path[0]
            rules_by_sw[sw].append(
                Rule(
                    switch=sw,
                    role="local",
                    in_port=topo.ports[sw][src],
                    eth_src=src_mac,
                    eth_dst=dst_mac,
                    match_vlan=None,
                    out_port=topo.ports[sw][dst],
                    set_vlan=None,
                    pop_vlan=False,
                    cookie=version,
                )
            )
            continue

        s_in = path[0]
        rules_by_sw[s_in].append(
            Rule(
                switch=s_in,
                role="ingress",
                in_port=topo.ports[s_in][src],
                eth_src=src_mac,
                eth_dst=dst_mac,
                match_vlan=None,
                out_port=topo.ports[s_in][path[1]],
                set_vlan=version,
                pop_vlan=False,
                cookie=version,
            )
        )

        for i in range(1, len(path) - 1):
            sw = path[i]
            rules_by_sw[sw].append(
                Rule(
                    switch=sw,
                    role="internal",
                    in_port=None,
                    eth_src=src_mac,
                    eth_dst=dst_mac,
                    match_vlan=version,
                    out_port=topo.ports[sw][path[i + 1]],
                    set_vlan=None,
                    pop_vlan=False,
                    cookie=version,
                )
            )

        s_out = path[-1]
        rules_by_sw[s_out].append(
            Rule(
                switch=s_out,
                role="egress",
                in_port=None,
                eth_src=src_mac,
                eth_dst=dst_mac,
                match_vlan=version,
                out_port=topo.ports[s_out][dst],
                set_vlan=None,
                pop_vlan=True,
                cookie=version,
            )
        )

    return {sw: frozenset(rules) for sw, rules in rules_by_sw.items()}