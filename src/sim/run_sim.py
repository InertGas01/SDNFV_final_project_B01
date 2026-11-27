from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics

from src.model.config import build_config
from src.model.scenarios import generate_scenario_routes
from src.model.update import build_2pc_plan
from src.sim.metrics import simulate_update
from src.topo.fattree import FatTreeTopology, build_dev_fattree, build_paper_fattree


def _mean_std(values: list[float]) -> tuple[float, float]:
    mean_v = statistics.mean(values)
    std_v = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean_v, std_v


def run_scale(
    scale_name: str,
    topo: FatTreeTopology,
    seeds: int,
) -> tuple[list[dict], list[dict]]:
    raw_rows: list[dict] = []
    summary_rows: list[dict] = []

    for scenario in ("hosts", "routes", "both"):
        sum_install_list: list[float] = []
        avg_install_list: list[float] = []
        sum_total_list: list[float] = []
        avg_total_list: list[float] = []
        max_oh_list: list[float] = []

        for seed in range(seeds):
            route_seq = generate_scenario_routes(topo, scenario=scenario, seed=seed, steps=2)
            configs = [build_config(topo, routes, version=i + 1) for i, routes in enumerate(route_seq)]

            step_results = []
            for step_idx in range(len(configs) - 1):
                plan = build_2pc_plan(
                    configs[step_idx],
                    configs[step_idx + 1],
                    old_version=step_idx + 1,
                    new_version=step_idx + 2,
                )
                res = simulate_update(configs[step_idx], configs[step_idx + 1], plan)
                if not res.final_matches_target:
                    raise RuntimeError(f"Final state mismatch on {scale_name}/{scenario}/seed={seed}")
                step_results.append(res)

            ops_install_sum = sum(r.ops_install for r in step_results)
            ops_total_sum = sum(r.ops_total for r in step_results)
            ops_install_avg = ops_install_sum / len(step_results)
            ops_total_avg = ops_total_sum / len(step_results)
            max_oh = max(r.max_overhead for r in step_results)

            sum_install_list.append(float(ops_install_sum))
            avg_install_list.append(float(ops_install_avg))
            sum_total_list.append(float(ops_total_sum))
            avg_total_list.append(float(ops_total_avg))
            max_oh_list.append(max_oh)

            raw_rows.append(
                {
                    "scale": scale_name,
                    "switches": len(topo.switches),
                    "hosts": len(topo.hosts),
                    "strategy": "2pc",
                    "scenario": scenario,
                    "seed": seed,
                    "ops_install_sum": ops_install_sum,
                    "ops_install_avg": round(ops_install_avg, 2),
                    "ops_total_sum": ops_total_sum,
                    "ops_total_avg": round(ops_total_avg, 2),
                    "max_overhead_pct": round(max_oh * 100.0, 2),
                }
            )

        inst_sum_m, inst_sum_s = _mean_std(sum_install_list)
        inst_avg_m, inst_avg_s = _mean_std(avg_install_list)
        tot_sum_m, tot_sum_s = _mean_std(sum_total_list)
        tot_avg_m, tot_avg_s = _mean_std(avg_total_list)
        oh_m, oh_s = _mean_std([x * 100.0 for x in max_oh_list])

        summary_rows.append(
            {
                "scale": scale_name,
                "strategy": "2pc",
                "scenario": scenario,
                "seeds": seeds,
                "ops_install_sum_mean": round(inst_sum_m, 1),
                "ops_install_sum_std": round(inst_sum_s, 1),
                "ops_install_avg_mean": round(inst_avg_m, 1),
                "ops_install_avg_std": round(inst_avg_s, 1),
                "ops_total_sum_mean": round(tot_sum_m, 1),
                "ops_total_sum_std": round(tot_sum_s, 1),
                "ops_total_avg_mean": round(tot_avg_m, 1),
                "ops_total_avg_std": round(tot_avg_s, 1),
                "max_overhead_pct_mean": round(oh_m, 2),
                "max_overhead_pct_std": round(oh_s, 2),
            }
        )

    return raw_rows, summary_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=("dev", "paper", "all"), default="all")
    parser.add_argument("--seeds", type=int, default=10)
    args = parser.parse_args()

    scales: list[tuple[str, FatTreeTopology]] = []
    if args.scale in ("dev", "all"):
        scales.append(("dev", build_dev_fattree()))
    if args.scale in ("paper", "all"):
        scales.append(("paper", build_paper_fattree()))

    all_raw: list[dict] = []
    all_summary: list[dict] = []

    for scale_name, topo in scales:
        raw, summary = run_scale(scale_name, topo, args.seeds)
        all_raw.extend(raw)
        all_summary.extend(summary)

    out_dir = Path("results")
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "baseline_2pc.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_raw[0].keys()))
        writer.writeheader()
        writer.writerows(all_raw)

    json_path = out_dir / "baseline_2pc_summary.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(all_summary, f, indent=2)

    header = (
        f"{'Scale':<7} {'Scenario':<8} {'Install(Sum)':>18} "
        f"{'Install(Avg)':>18} {'Total(Avg)':>18} {'Max OH (%)':>15}"
    )
    print(header)
    print("-" * len(header))
    for row in all_summary:
        print(
            f"{row['scale']:<7} {row['scenario']:<8} "
            f"{row['ops_install_sum_mean']:>10.0f} ±{row['ops_install_sum_std']:<6.0f} "
            f"{row['ops_install_avg_mean']:>10.0f} ±{row['ops_install_avg_std']:<6.0f} "
            f"{row['ops_total_avg_mean']:>10.0f} ±{row['ops_total_avg_std']:<6.0f} "
            f"{row['max_overhead_pct_mean']:>7.2f} ±{row['max_overhead_pct_std']:<5.2f}"
        )


if __name__ == "__main__":
    main()