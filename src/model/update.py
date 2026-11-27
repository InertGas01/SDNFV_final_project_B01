from __future__ import annotations

from dataclasses import dataclass

from src.model.config import Config, Rule


@dataclass(frozen=True)
class FlowOp:
    op_type: str
    rule: Rule


@dataclass(frozen=True)
class Phase:
    name: str
    ops: tuple[FlowOp, ...]
    barrier: bool = True
    drain: bool = False


@dataclass(frozen=True)
class UpdatePlan:
    old_version: int
    new_version: int
    phases: tuple[Phase, ...]


def build_2pc_plan(
    old_cfg: Config,
    new_cfg: Config,
    old_version: int,
    new_version: int,
) -> UpdatePlan:
    phase1_ops: list[FlowOp] = []
    phase2_ops: list[FlowOp] = []
    phase3_ops: list[FlowOp] = []

    switches = sorted(set(old_cfg.keys()) | set(new_cfg.keys()))

    for sw in switches:
        old_rules = old_cfg.get(sw, frozenset())
        new_rules = new_cfg.get(sw, frozenset())

        old_ingress = {r.match_key(): r for r in old_rules if r.role in ("ingress", "local")}
        new_ingress = {r.match_key(): r for r in new_rules if r.role in ("ingress", "local")}

        for r in new_rules:
            if r.role in ("internal", "egress"):
                phase1_ops.append(FlowOp("ADD", r))

        for mk, r in new_ingress.items():
            if mk in old_ingress:
                phase2_ops.append(FlowOp("MODIFY", r))
            else:
                phase2_ops.append(FlowOp("ADD", r))

        for r in old_rules:
            if r.role in ("internal", "egress"):
                phase3_ops.append(FlowOp("DELETE", r))

        for mk, r in old_ingress.items():
            if mk not in new_ingress:
                phase3_ops.append(FlowOp("DELETE", r))

    return UpdatePlan(
        old_version=old_version,
        new_version=new_version,
        phases=(
            Phase("phase1_internal", tuple(phase1_ops), barrier=True, drain=False),
            Phase("phase2_ingress", tuple(phase2_ops), barrier=True, drain=True),
            Phase("phase3_cleanup", tuple(phase3_ops), barrier=True, drain=False),
        ),
    )