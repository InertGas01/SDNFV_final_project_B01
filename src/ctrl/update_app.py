from __future__ import annotations

import json
import os
from pathlib import Path

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    DEAD_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from os_ken.lib import hub
from os_ken.ofproto import ofproto_v1_3

from src.model.config import Config, Rule, build_config
from src.model.scenarios import generate_scenario_routes
from src.model.update import UpdatePlan, build_2pc_plan
from src.sim.metrics import simulate_update
from src.topo.fattree import build_dev_fattree, build_paper_fattree


class TwoPhaseUpdateApp(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    FLOW_PRIORITY = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scale = os.environ.get("SDN_SCALE", "dev")
        self.scenario = os.environ.get("SDN_SCENARIO", "routes")
        self.seed = int(os.environ.get("SDN_SEED", "0"))
        self.drain_sec = float(os.environ.get("SDN_DRAIN_SEC", "2.0"))
        self.init_wait_sec = float(os.environ.get("SDN_INIT_WAIT_SEC", "2.0"))

        self.topo = build_paper_fattree() if self.scale == "paper" else build_dev_fattree()
        self.dpid_to_sw = {dpid: sw for sw, dpid in self.topo.switch_dpid.items()}
        self.sw_to_dpid = dict(self.topo.switch_dpid)

        self.datapaths: dict[int, object] = {}
        self.started = False

        self.barrier_pending: set[int] = set()
        self.barrier_event = hub.Event()

        self.stats_pending: set[int] = set()
        self.stats_counts: dict[int, int] = {}
        self.stats_event = hub.Event()

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        clear_mod = parser.OFPFlowMod(
            datapath=datapath,
            table_id=ofproto.OFPTT_ALL,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=parser.OFPMatch(),
        )
        datapath.send_msg(clear_mod)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if datapath.id is None:
            return
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
            self.logger.info(
                "Switch connected: dpid=%s (%d/%d)",
                datapath.id,
                len(self.datapaths),
                len(self.topo.switches),
            )
            if not self.started and len(self.datapaths) == len(self.topo.switches):
                self.started = True
                hub.spawn(self._run_experiment)
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        if dpid in self.barrier_pending:
            self.barrier_pending.remove(dpid)
            if not self.barrier_pending:
                self.barrier_event.set()

    @set_ev_cls(ofp_event.EventOFPAggregateStatsReply, MAIN_DISPATCHER)
    def aggregate_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        body = ev.msg.body
        flow_count = body[0].flow_count if isinstance(body, list) else body.flow_count
        self.stats_counts[dpid] = int(flow_count)
        if dpid in self.stats_pending:
            self.stats_pending.remove(dpid)
            if not self.stats_pending:
                self.stats_event.set()

    def _build_match(self, datapath, rule: Rule):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        kwargs = {
            "eth_src": rule.eth_src,
            "eth_dst": rule.eth_dst,
        }
        if rule.in_port is not None:
            kwargs["in_port"] = rule.in_port
        if rule.match_vlan is not None:
            kwargs["vlan_vid"] = ofproto.OFPVID_PRESENT | rule.match_vlan
        else:
            kwargs["vlan_vid"] = ofproto.OFPVID_NONE
        return parser.OFPMatch(**kwargs)

    def _build_instructions(self, datapath, rule: Rule):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        actions = []
        if rule.set_vlan is not None:
            actions.append(parser.OFPActionPushVlan(0x8100))
            actions.append(
                parser.OFPActionSetField(vlan_vid=ofproto.OFPVID_PRESENT | rule.set_vlan)
            )
        if rule.pop_vlan:
            actions.append(parser.OFPActionPopVlan())
        actions.append(parser.OFPActionOutput(rule.out_port))
        return [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

    def _send_rule_op(self, op_type: str, rule: Rule) -> None:
        dpid = self.sw_to_dpid[rule.switch]
        datapath = self.datapaths[dpid]
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = self._build_match(datapath, rule)

        if op_type == "ADD":
            mod = parser.OFPFlowMod(
                datapath=datapath,
                table_id=0,
                command=ofproto.OFPFC_ADD,
                cookie=rule.cookie,
                priority=self.FLOW_PRIORITY,
                match=match,
                instructions=self._build_instructions(datapath, rule),
            )
        elif op_type == "MODIFY":
            mod = parser.OFPFlowMod(
                datapath=datapath,
                table_id=0,
                command=ofproto.OFPFC_MODIFY_STRICT,
                cookie=rule.cookie,
                cookie_mask=0,
                priority=self.FLOW_PRIORITY,
                match=match,
                instructions=self._build_instructions(datapath, rule),
            )
        elif op_type == "DELETE":
            mod = parser.OFPFlowMod(
                datapath=datapath,
                table_id=0,
                command=ofproto.OFPFC_DELETE_STRICT,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                priority=self.FLOW_PRIORITY,
                match=match,
            )
        else:
            raise ValueError(f"Unsupported op_type: {op_type}")

        datapath.send_msg(mod)

    def _barrier_all(self, timeout: float = 60.0) -> None:
        dpids = list(self.datapaths.keys())
        self.barrier_pending = set(dpids)
        self.barrier_event = hub.Event()
        for dpid in dpids:
            dp = self.datapaths[dpid]
            dp.send_msg(dp.ofproto_parser.OFPBarrierRequest(dp))
        if not self.barrier_event.wait(timeout=timeout):
            raise TimeoutError(f"Barrier timeout waiting for dpids: {sorted(self.barrier_pending)}")

    def _query_flow_counts(self, timeout: float = 60.0) -> dict[str, int]:
        dpids = list(self.datapaths.keys())
        self.stats_pending = set(dpids)
        self.stats_counts = {}
        self.stats_event = hub.Event()
        for dpid in dpids:
            dp = self.datapaths[dpid]
            ofproto = dp.ofproto
            parser = dp.ofproto_parser
            req = parser.OFPAggregateStatsRequest(
                dp,
                flags=0,
                table_id=ofproto.OFPTT_ALL,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                cookie=0,
                cookie_mask=0,
                match=parser.OFPMatch(),
            )
            dp.send_msg(req)
        if not self.stats_event.wait(timeout=timeout):
            raise TimeoutError(f"Stats timeout waiting for dpids: {sorted(self.stats_pending)}")
        return {self.dpid_to_sw[dpid]: count for dpid, count in self.stats_counts.items()}

    def _install_initial_config(self, cfg: Config) -> dict[str, int]:
        for sw in self.topo.switches:
            for rule in cfg.get(sw, frozenset()):
                self._send_rule_op("ADD", rule)
        self._barrier_all()
        return self._query_flow_counts()

    def _execute_plan(self, old_cfg: Config, new_cfg: Config, plan: UpdatePlan) -> dict:
        sim_res = simulate_update(old_cfg, new_cfg, plan)

        counts = self._query_flow_counts()
        peak_measured = dict(counts)
        sent_add = 0
        sent_modify = 0
        sent_delete = 0

        for phase in plan.phases:
            for op in phase.ops:
                self._send_rule_op(op.op_type, op.rule)
                if op.op_type == "ADD":
                    sent_add += 1
                elif op.op_type == "MODIFY":
                    sent_modify += 1
                elif op.op_type == "DELETE":
                    sent_delete += 1
            if phase.barrier:
                self._barrier_all()
            counts = self._query_flow_counts()
            for sw, cnt in counts.items():
                if cnt > peak_measured[sw]:
                    peak_measured[sw] = cnt
            if phase.drain and self.drain_sec > 0:
                hub.sleep(self.drain_sec)

        measured_overheads: dict[str, float] = {}
        for sw in self.topo.switches:
            denom = max(len(old_cfg.get(sw, frozenset())), len(new_cfg.get(sw, frozenset())))
            if denom == 0:
                continue
            measured_overheads[sw] = (peak_measured[sw] - denom) / denom

        measured_max_oh = max(measured_overheads.values()) if measured_overheads else 0.0
        expected_final = {sw: len(new_cfg.get(sw, frozenset())) for sw in self.topo.switches}

        return {
            "old_version": plan.old_version,
            "new_version": plan.new_version,
            "sim_ops_install": sim_res.ops_install,
            "measured_ops_install": sent_add + sent_modify,
            "sim_ops_total": sim_res.ops_total,
            "measured_ops_total": sent_add + sent_modify + sent_delete,
            "sim_max_overhead_pct": round(sim_res.max_overhead * 100.0, 4),
            "measured_max_overhead_pct": round(measured_max_oh * 100.0, 4),
            "ops_exact_match": (
                sent_add + sent_modify == sim_res.ops_install
                and sent_add + sent_modify + sent_delete == sim_res.ops_total
            ),
            "peak_exact_match": peak_measured == sim_res.peak_rules,
            "final_count_exact_match": counts == expected_final,
        }

    def _run_experiment(self) -> None:
        try:
            hub.sleep(1.0)
            self._barrier_all()

            route_seq = generate_scenario_routes(
                self.topo, scenario=self.scenario, seed=self.seed, steps=2
            )
            configs = [
                build_config(self.topo, routes, version=i + 1)
                for i, routes in enumerate(route_seq)
            ]

            self.logger.info(
                "Installing initial C0 (%s / %s / seed=%d)...",
                self.scale,
                self.scenario,
                self.seed,
            )
            c0_counts = self._install_initial_config(configs[0])
            expected_c0 = {sw: len(configs[0][sw]) for sw in self.topo.switches}
            c0_match = c0_counts == expected_c0
            self.logger.info("C0 installed. Exact count match: %s", c0_match)

            if self.init_wait_sec > 0:
                hub.sleep(self.init_wait_sec)

            step_reports = []
            for idx in range(len(configs) - 1):
                plan = build_2pc_plan(
                    configs[idx],
                    configs[idx + 1],
                    old_version=idx + 1,
                    new_version=idx + 2,
                )
                report = self._execute_plan(configs[idx], configs[idx + 1], plan)
                step_reports.append(report)
                self.logger.info(
                    "Step C%d->C%d | Ops(sim=%d, real=%d) | MaxOH(sim=%.2f%%, real=%.2f%%) | peak_match=%s",
                    idx,
                    idx + 1,
                    report["sim_ops_total"],
                    report["measured_ops_total"],
                    report["sim_max_overhead_pct"],
                    report["measured_max_overhead_pct"],
                    report["peak_exact_match"],
                )

            all_passed = c0_match and all(
                r["ops_exact_match"] and r["peak_exact_match"] and r["final_count_exact_match"]
                for r in step_reports
            )

            out_dir = Path("results")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"mininet_verify_{self.scale}_{self.scenario}_seed{self.seed}.json"
            payload = {
                "scale": self.scale,
                "scenario": self.scenario,
                "seed": self.seed,
                "c0_exact_match": c0_match,
                "all_exact_match": all_passed,
                "steps": step_reports,
            }
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.logger.info("Verification complete (all_exact_match=%s) -> %s", all_passed, out_path)
        except Exception:
            self.logger.exception("Experiment failed")