# SDNFV Final Project — B01: Abstractions for Network Update

重現 SIGCOMM 2012 論文 *Abstractions for Network Update*（Reitblatt et al.）Table 2 中 **Fat Tree × Routing** 的 2PC 與 Subset 更新效能，並延伸探討 Soft Timeout 對 2PC 流表開銷（Overhead）的優化。詳細實驗設計見 [`reproduction-plan.md`](reproduction-plan.md)。

## 專案結構

```text
.
├── Dockerfile                  # Ubuntu 22.04 + Mininet + OVS + os-ken 環境
├── pytest.ini                  # pytest 路徑設定
├── reproduction-plan.md        # 實驗設計與分工規劃
├── src/
│   ├── topo/fattree.py         # Fat Tree 拓撲（dev: 20 sw/16 hosts, paper: 48 sw/192 hosts）
│   ├── model/
│   │   ├── routing.py          # 決定性最短路徑與繞路計算
│   │   ├── config.py           # OpenFlow 1.3 規則與 VLAN 版本標記模型
│   │   ├── update.py           # UpdatePlan 與 2PC（後續於此擴充 Subset）
│   │   └── scenarios.py        # C0 -> C1 -> C2 情境產生器（hosts / routes / both）
│   ├── sim/
│   │   ├── metrics.py          # 離線流表模擬與 Ops / Overhead 計算
│   │   └── run_sim.py          # 多 seed 離線批次實驗腳本
│   ├── ctrl/
│   │   ├── update_app.py       # os-ken 2PC 控制器與實測流表交叉驗證
│   │   └── run_controller.py   # os-ken 啟動入口
│   └── exp/
│       └── run_mininet.py      # Mininet 拓撲啟動與更新期間 ping 探測
├── tests/
│   └── test_baseline.py        # 拓撲、路由、手算 Overhead 與端對端單元測試
└── results/                    # 離線模擬 CSV/JSON 與 Mininet 交叉驗證結果
```

---

## 1. 離線模擬與單元測試（本機 Python）

不需要 Mininet 或 OVS 即可執行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install networkx pytest matplotlib
```

執行單元測試：

```bash
pytest -v
```

執行離線模擬（輸出至 `results/baseline_2pc.csv` 與 `results/baseline_2pc_summary.json`）：

```bash
# 快速測試 dev-scale
python3 -m src.sim.run_sim --scale dev --seeds 3

# 完整跑 dev-scale 與 paper-scale 各 10 seeds
python3 -m src.sim.run_sim --scale all --seeds 10
```

---

## 2. Mininet + `os-ken` 交叉驗證（Docker 環境）

為避免 WSL2 缺少 OVS kernel module 與 Python 版本相容問題，使用 `Dockerfile` 提供的固定環境（OVS userspace 模式）。

### 建置映像檔

```bash
docker build -t sdnfv-b01 .
```

### 終端機 A：啟動容器、開啟 OVS 與 `os-ken` 控制器

```bash
docker run -it --rm --privileged --name sdnfv-b01-run -v "$PWD:/workspace" sdnfv-b01 bash
```

進入容器後啟動 OVS（userspace）：

```bash
/usr/share/openvswitch/scripts/ovs-ctl --no-ovs-vswitchd start --system-id=random
ovs-vswitchd --pidfile --detach --log-file=/var/log/openvswitch/ovs-vswitchd.log
ovs-vsctl show
```

啟動控制器（`SDN_SCENARIO` 可設為 `routes`、`hosts`、`both`）：

```bash
PYTHONPATH=. SDN_SCALE=dev SDN_SCENARIO=routes SDN_SEED=0 python3 -m src.ctrl.run_controller
```

### 終端機 B：連進同一容器執行 Mininet

```bash
docker exec -it sdnfv-b01-run bash
PYTHONPATH=. python3 src/exp/run_mininet.py --scale dev --scenario routes --seed 0 --datapath user
```

> **切換情境提醒**：每次實驗結束後，請在終端機 A 按 `Ctrl+C` 重啟控制器，再於終端機 B 執行下一個情境（兩邊的 `SDN_SCALE` / `SDN_SCENARIO` / `SDN_SEED` 需一致）。

驗證完成後結果會存於 `results/mininet_verify_<scale>_<scenario>_seed<seed>.json`，確認 `"all_exact_match": true` 即代表實測 Ops、每台交換機流表 Peak 與最終規則數皆與離線模擬器完全一致。

---

## 3. 後續開發接軌點

* **Subset 更新策略**：在 `src/model/update.py` 依照相同的 `UpdatePlan` / `Phase` / `FlowOp` 介面實作 `build_subset_plan()`，即可直接共用 `src/sim/metrics.py` 與 `src/ctrl/update_app.py`。
* **Soft Timeout 延伸實驗**：可在 `src/ctrl/update_app.py` 擴充 `idle_timeout` 與定時輪詢 `OFPAggregateStatsRequest`，量測 Overhead 隨時間變化曲線（對比設計詳見 `reproduction-plan.md` §9.3）。