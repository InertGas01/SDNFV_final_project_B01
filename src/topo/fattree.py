from __future__ import annotations

from dataclasses import dataclass
import networkx as nx


@dataclass
class FatTreeTopology:
    k: int
    pods: int
    hosts_per_edge: int
    graph: nx.Graph
    switch_graph: nx.Graph
    cores: list[str]
    aggs: list[str]
    edges: list[str]
    switches: list[str]
    hosts: list[str]
    host_to_edge: dict[str, str]
    host_mac: dict[str, str]
    host_ip: dict[str, str]
    switch_dpid: dict[str, int]
    ports: dict[str, dict[str, int]]
    port_types: dict[str, dict[int, str]]

    def to_mininet_topo(self):
        from mininet.topo import Topo

        ft = self

        class GeneratedFatTreeTopo(Topo):
            def build(self):
                for sw in ft.switches:
                    self.addSwitch(sw, dpid=f"{ft.switch_dpid[sw]:016x}")
                for h in ft.hosts:
                    self.addHost(h, mac=ft.host_mac[h], ip=f"{ft.host_ip[h]}/8")
                seen: set[tuple[str, str]] = set()
                for u, v in ft.graph.edges():
                    key = (u, v) if u < v else (v, u)
                    if key in seen:
                        continue
                    seen.add(key)
                    p1 = ft.ports[u][v] if u in ft.ports else None
                    p2 = ft.ports[v][u] if v in ft.ports else None
                    self.addLink(u, v, port1=p1, port2=p2)

        return GeneratedFatTreeTopo()


def build_fattree(k: int = 8, pods: int = 4, hosts_per_edge: int = 12) -> FatTreeTopology:
    if k % 2 != 0 or k < 2:
        raise ValueError("k must be an even integer >= 2")
    if pods < 1 or pods > k:
        raise ValueError("pods must be between 1 and k")

    half = k // 2
    graph = nx.Graph()
    switch_graph = nx.Graph()

    cores: list[str] = []
    aggs: list[str] = []
    edges: list[str] = []
    hosts: list[str] = []
    host_to_edge: dict[str, str] = {}
    host_mac: dict[str, str] = {}
    host_ip: dict[str, str] = {}
    switch_dpid: dict[str, int] = {}
    ports: dict[str, dict[str, int]] = {}
    port_types: dict[str, dict[int, str]] = {}

    def register_switch(name: str, layer: str) -> None:
        graph.add_node(name, kind="switch", layer=layer)
        switch_graph.add_node(name, layer=layer)
        ports[name] = {}
        port_types[name] = {}
        switch_dpid[name] = len(switch_dpid) + 1

    def connect(u: str, v: str, is_host_link: bool) -> None:
        graph.add_edge(u, v)
        if not is_host_link:
            switch_graph.add_edge(u, v)
        if u in ports:
            p_u = len(ports[u]) + 1
            ports[u][v] = p_u
            port_types[u][p_u] = "ingress" if is_host_link else "internal"
        if v in ports:
            p_v = len(ports[v]) + 1
            ports[v][u] = p_v
            port_types[v][p_v] = "ingress" if is_host_link else "internal"

    for g in range(half):
        for i in range(half):
            c_name = f"c_{g}_{i}"
            cores.append(c_name)
            register_switch(c_name, "core")

    host_index = 0
    for p in range(pods):
        pod_aggs: list[str] = []
        pod_edges: list[str] = []

        for a in range(half):
            a_name = f"a_{p}_{a}"
            aggs.append(a_name)
            pod_aggs.append(a_name)
            register_switch(a_name, "agg")

        for e in range(half):
            e_name = f"e_{p}_{e}"
            edges.append(e_name)
            pod_edges.append(e_name)
            register_switch(e_name, "edge")

        for a_idx, a_name in enumerate(pod_aggs):
            for i in range(half):
                connect(f"c_{a_idx}_{i}", a_name, is_host_link=False)

        for a_name in pod_aggs:
            for e_name in pod_edges:
                connect(a_name, e_name, is_host_link=False)

        for e_idx, e_name in enumerate(pod_edges):
            for h in range(hosts_per_edge):
                host_index += 1
                h_name = f"h_{p}_{e_idx}_{h}"
                hosts.append(h_name)
                host_to_edge[h_name] = e_name
                host_mac[h_name] = f"00:00:00:00:{(host_index >> 8) & 0xff:02x}:{host_index & 0xff:02x}"
                host_ip[h_name] = f"10.{p}.{e_idx}.{h + 1}"
                graph.add_node(h_name, kind="host")
                connect(e_name, h_name, is_host_link=True)

    switches = cores + aggs + edges
    return FatTreeTopology(
        k=k,
        pods=pods,
        hosts_per_edge=hosts_per_edge,
        graph=graph,
        switch_graph=switch_graph,
        cores=cores,
        aggs=aggs,
        edges=edges,
        switches=switches,
        hosts=hosts,
        host_to_edge=host_to_edge,
        host_mac=host_mac,
        host_ip=host_ip,
        switch_dpid=switch_dpid,
        ports=ports,
        port_types=port_types,
    )


def build_dev_fattree() -> FatTreeTopology:
    return build_fattree(k=4, pods=4, hosts_per_edge=2)


def build_paper_fattree() -> FatTreeTopology:
    return build_fattree(k=8, pods=4, hosts_per_edge=12)