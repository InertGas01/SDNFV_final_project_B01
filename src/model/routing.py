from __future__ import annotations

from collections.abc import Iterable
import hashlib
import networkx as nx

from src.topo.fattree import FatTreeTopology

PathCache = dict[tuple[str, str, frozenset[str]], list[list[str]]]


def pick_deterministic_path(paths: list[list[str]], src: str, dst: str) -> list[str]:
    ordered = sorted(paths)
    digest = hashlib.md5(f"{src}->{dst}".encode("utf-8")).digest()
    idx = int.from_bytes(digest[:8], "big") % len(ordered)
    return ordered[idx]


def compute_pair_path(
    topo: FatTreeTopology,
    src: str,
    dst: str,
    disabled_switches: Iterable[str] | None = None,
    cache: PathCache | None = None,
) -> list[str]:
    e_src = topo.host_to_edge[src]
    e_dst = topo.host_to_edge[dst]
    if e_src == e_dst:
        return [e_src]

    banned = frozenset(disabled_switches) if disabled_switches else frozenset()
    key = (e_src, e_dst, banned)

    if cache is not None and key in cache:
        candidates = cache[key]
    else:
        if banned:
            allowed = [sw for sw in topo.switches if sw not in banned]
            sub = topo.switch_graph.subgraph(allowed)
            candidates = list(nx.all_shortest_paths(sub, e_src, e_dst))
        else:
            candidates = list(nx.all_shortest_paths(topo.switch_graph, e_src, e_dst))
        if cache is not None:
            cache[key] = candidates

    return pick_deterministic_path(candidates, src, dst)


def compute_routing(
    topo: FatTreeTopology,
    active_hosts: Iterable[str],
    disabled_switches_by_pair: dict[tuple[str, str], set[str]] | None = None,
) -> dict[tuple[str, str], list[str]]:
    active_list = sorted(active_hosts)
    cache: PathCache = {}
    routes: dict[tuple[str, str], list[str]] = {}

    for src in active_list:
        for dst in active_list:
            if src == dst:
                continue
            banned = disabled_switches_by_pair.get((src, dst)) if disabled_switches_by_pair else None
            routes[(src, dst)] = compute_pair_path(topo, src, dst, banned, cache)

    return routes