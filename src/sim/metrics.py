from __future__ import annotations

from dataclasses import dataclass

from src.model.config import Config, Rule
from src.model.update import UpdatePlan


@dataclass
class SimulationResult:
    ops_add: int
    ops_modify: int
    ops_delete: int
    ops_install: int
    ops_total: int
    peak_rules: dict[str, int]
    switch_overheads: dict[str, float]
    max_overhead: float
    final_matches_target: bool


def simulate_update(
    old_cfg: Config,
    new_cfg: Config,
    plan: UpdatePlan,
) -> SimulationResult:
    switches = sorted(set(old_cfg.keys()) | set(new_cfg.keys()))
    tables: dict[str, dict[tuple[int | None, str, str, int | None], Rule]] = {
        sw: {r.match_key(): r for r in old_cfg.get(sw, frozenset())}
        for sw in switches
    }
    peak_rules: dict[str, int] = {sw: len(tables[sw]) for sw in switches}

    ops_add = 0
    ops_modify = 0
    ops_delete = 0

    for phase in plan.phases:
        for op in phase.ops:
            sw = op.rule.switch
            mk = op.rule.match_key()
            tbl = tables[sw]

            if op.op_type == "ADD":
                tbl[mk] = op.rule
                ops_add += 1
            elif op.op_type == "MODIFY":
                tbl[mk] = op.rule
                ops_modify += 1
            elif op.op_type == "DELETE":
                tbl.pop(mk, None)
                ops_delete += 1
            else:
                raise ValueError(f"Unknown op_type: {op.op_type}")

            if len(tbl) > peak_rules[sw]:
                peak_rules[sw] = len(tbl)

    switch_overheads: dict[str, float] = {}
    for sw in switches:
        denom = max(len(old_cfg.get(sw, frozenset())), len(new_cfg.get(sw, frozenset())))
        if denom == 0:
            continue
        switch_overheads[sw] = (peak_rules[sw] - denom) / denom

    max_overhead = max(switch_overheads.values()) if switch_overheads else 0.0
    final_matches_target = all(
        frozenset(tables[sw].values()) == new_cfg.get(sw, frozenset())
        for sw in switches
    )

    return SimulationResult(
        ops_add=ops_add,
        ops_modify=ops_modify,
        ops_delete=ops_delete,
        ops_install=ops_add + ops_modify,
        ops_total=ops_add + ops_modify + ops_delete,
        peak_rules=peak_rules,
        switch_overheads=switch_overheads,
        max_overhead=max_overhead,
        final_matches_target=final_matches_target,
    )