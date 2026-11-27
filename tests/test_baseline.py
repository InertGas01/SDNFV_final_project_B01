from __future__ import annotations

import networkx as nx

from src.model.config import Rule, build_config
from src.model.routing import compute_pair_path, compute_routing
from src.model.scenarios import generate_scenario_routes
from src.model.update import build_2pc_plan
from src.sim.metrics import simulate_update
from src.topo.fattree import build_dev_fattree, build_paper_fattree


def test_dev_fattree_counts_and_connectivity() -> None:
    topo = build_dev_fattree()
    assert len(topo.cores) == 4
    assert len(topo.aggs) == 8
    assert len(topo.edges) == 8
    assert len(topo.switches) == 20
    assert len(topo.hosts) == 16
    assert nx.is_connected(topo.graph)
    assert nx.is_connected(topo.switch_graph)


def test_paper_fattree_counts_and_ports() -> None:
    topo = build_paper_fattree()
    assert len(topo.cores) == 16
    assert len(topo.aggs) == 16
    assert len(topo.edges) == 16
    assert len(topo.switches) == 48
    assert len(topo.hosts) == 192
    assert nx.is_connected(topo.graph)

    for e in topo.edges:
        ingress_ports = [p for p, t in topo.port_types[e].items() if t == "ingress"]
        internal_ports = [p for p, t in topo.port_types[e].items() if t == "internal"]
        assert len(ingress_ports) == 12
        assert len(internal_ports) == 4


def test_deterministic_routing_and_reroute() -> None:
    topo = build_dev_fattree()
    r1 = compute_routing(topo, topo.hosts)
    r2 = compute_routing(topo, topo.hosts)
    assert r1 == r2
    assert len(r1) == 16 * 15

    src, dst = "h_0_0_0", "h_1_0_0"
    orig_path = r1[(src, dst)]
    assert len(orig_path) == 5
    banned = orig_path[2]
    alt_path = compute_pair_path(topo, src, dst, disabled_switches={banned})
    assert banned not in alt_path
    assert len(alt_path) == 5


def test_hand_calculated_2pc_overhead() -> None:
    r_old_in = Rule("s1", "ingress", 1, "aa", "bb", None, 2, 1, False, 1)
    r_old_mid = Rule("s2", "internal", None, "aa", "bb", 1, 2, None, False, 1)
    r_old_out = Rule("s3", "egress", None, "aa", "bb", 1, 2, None, True, 1)

    r_new_in = Rule("s1", "ingress", 1, "aa", "bb", None, 2, 2, False, 2)
    r_new_mid = Rule("s2", "internal", None, "aa", "bb", 2, 2, None, False, 2)
    r_new_out = Rule("s3", "egress", None, "aa", "bb", 2, 2, None, True, 2)

    old_cfg = {
        "s1": frozenset([r_old_in]),
        "s2": frozenset([r_old_mid]),
        "s3": frozenset([r_old_out]),
    }
    new_cfg = {
        "s1": frozenset([r_new_in]),
        "s2": frozenset([r_new_mid]),
        "s3": frozenset([r_new_out]),
    }

    plan = build_2pc_plan(old_cfg, new_cfg, old_version=1, new_version=2)
    res = simulate_update(old_cfg, new_cfg, plan)

    assert res.final_matches_target is True
    assert res.ops_add == 2
    assert res.ops_modify == 1
    assert res.ops_delete == 2
    assert res.ops_install == 3
    assert res.ops_total == 5
    assert res.switch_overheads["s1"] == 0.0
    assert res.switch_overheads["s2"] == 1.0
    assert res.switch_overheads["s3"] == 1.0
    assert res.max_overhead == 1.0


def test_dev_scale_scenarios_end_to_end() -> None:
    topo = build_dev_fattree()
    for scenario in ("hosts", "routes", "both"):
        seq = generate_scenario_routes(topo, scenario=scenario, seed=0, steps=2)
        c0 = build_config(topo, seq[0], version=1)
        c1 = build_config(topo, seq[1], version=2)
        plan = build_2pc_plan(c0, c1, old_version=1, new_version=2)
        res = simulate_update(c0, c1, plan)
        assert res.final_matches_target is True
        if scenario == "routes":
            assert res.max_overhead >= 0.90