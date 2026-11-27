from __future__ import annotations

import random

from src.model.routing import PathCache, compute_pair_path, compute_routing
from src.topo.fattree import FatTreeTopology


def _toggle_hosts(all_hosts: list[str], active_hosts: set[str], rng: random.Random) -> set[str]:
    frac = rng.uniform(0.10, 0.20)
    count = max(1, int(round(len(all_hosts) * frac)))
    toggled = rng.sample(all_hosts, count)
    new_active = set(active_hosts)
    for h in toggled:
        if h in new_active:
            new_active.remove(h)
        else:
            new_active.add(h)
    if len(new_active) < 2:
        for h in all_hosts:
            new_active.add(h)
            if len(new_active) >= 2:
                break
    return new_active


def _next_step_routes(
    topo: FatTreeTopology,
    prev_routes: dict[tuple[str, str], list[str]],
    new_active: set[str],
    apply_routes: bool,
    rng: random.Random,
    cache: PathCache,
) -> dict[tuple[str, str], list[str]]:
    reroute_banned: dict[tuple[str, str], set[str]] = {}

    if apply_routes:
        eligible = [
            pair
            for pair, path in prev_routes.items()
            if pair[0] in new_active and pair[1] in new_active and len(path) >= 3
        ]
        if eligible:
            count = max(1, int(round(len(eligible) * 0.20)))
            chosen = rng.sample(eligible, count)
            for pair in chosen:
                banned_sw = rng.choice(prev_routes[pair][1:-1])
                reroute_banned[pair] = {banned_sw}

    active_list = sorted(new_active)
    next_routes: dict[tuple[str, str], list[str]] = {}

    for src in active_list:
        for dst in active_list:
            if src == dst:
                continue
            pair = (src, dst)
            if pair in reroute_banned:
                next_routes[pair] = compute_pair_path(
                    topo, src, dst, disabled_switches=reroute_banned[pair], cache=cache
                )
            elif pair in prev_routes:
                next_routes[pair] = prev_routes[pair]
            else:
                next_routes[pair] = compute_pair_path(topo, src, dst, cache=cache)

    return next_routes


def generate_scenario_routes(
    topo: FatTreeTopology,
    scenario: str,
    seed: int,
    steps: int = 2,
) -> list[dict[tuple[str, str], list[str]]]:
    if scenario not in ("hosts", "routes", "both"):
        raise ValueError(f"Unknown scenario: {scenario}")

    apply_hosts = scenario in ("hosts", "both")
    apply_routes = scenario in ("routes", "both")

    rng = random.Random(seed)
    cache: PathCache = {}
    all_hosts = list(topo.hosts)
    active_hosts = set(all_hosts)

    c0 = compute_routing(topo, active_hosts)
    sequence = [c0]
    prev_routes = c0

    for _ in range(steps):
        if apply_hosts:
            active_hosts = _toggle_hosts(all_hosts, active_hosts, rng)
        next_routes = _next_step_routes(
            topo=topo,
            prev_routes=prev_routes,
            new_active=active_hosts,
            apply_routes=apply_routes,
            rng=rng,
            cache=cache,
        )
        sequence.append(next_routes)
        prev_routes = next_routes

    return sequence