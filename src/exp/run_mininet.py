from __future__ import annotations

import signal
import argparse
from functools import partial
import json
from pathlib import Path
import time

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

from src.topo.fattree import build_dev_fattree, build_paper_fattree


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=("dev", "paper"), default="dev")
    parser.add_argument("--scenario", choices=("hosts", "routes", "both"), default="routes")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    parser.add_argument("--datapath", choices=("kernel", "user"), default="kernel")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--cli", action="store_true")
    args = parser.parse_args()

    result_path = Path("results") / f"mininet_verify_{args.scale}_{args.scenario}_seed{args.seed}.json"
    if result_path.exists():
        result_path.unlink()

    setLogLevel("info")
    ft = build_paper_fattree() if args.scale == "paper" else build_dev_fattree()
    topo = ft.to_mininet_topo()

    switch_cls = partial(OVSSwitch, protocols="OpenFlow13", datapath=args.datapath)
    controller = RemoteController("c0", ip=args.controller_ip, port=args.controller_port)

    net = Mininet(
        topo=topo,
        switch=switch_cls,
        controller=controller,
        autoStaticArp=True,
        autoSetMacs=False,
    )

    net.start()
    try:
        if args.cli:
            CLI(net)
            return

        h_src = net.get("h_0_0_0")
        h_dst = net.get("h_1_0_0")
        ping_proc = h_src.popen(["ping", "-i", "0.2", ft.host_ip["h_1_0_0"]])

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if result_path.exists():
                break
            time.sleep(0.5)

        ping_proc.send_signal(signal.SIGINT)
        stdout, _ = ping_proc.communicate(timeout=5)
        ping_text = stdout.decode("utf-8", errors="ignore")
        summary_lines = [
            line for line in ping_text.splitlines() if "packets transmitted" in line or "rtt" in line
        ]

        print("=== Ping Probe During Update ===")
        for line in summary_lines:
            print(line)

        if not result_path.exists():
            raise TimeoutError(f"Timed out waiting for controller output: {result_path}")

        report = json.loads(result_path.read_text(encoding="utf-8"))
        print("=== Cross-Validation Result ===")
        print(json.dumps(report, indent=2))
    finally:
        net.stop()


if __name__ == "__main__":
    main()