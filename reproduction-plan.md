# 重現計畫：Fat Tree × Routing × 2PC（Reitblatt et al., SIGCOMM'12, Table 2）

> 專題 B01 — Abstractions for Network Update
> 環境：Mininet + Ryu（依選題報告）
> 建立日期：2026-11-25

---

## 1. 重現目標

重現論文 Table 2 中 **Routing / Fat Tree** 三列，核心為 2PC 欄位，Subset 欄位作為對照組（同一套程式只差 update 策略，成本低且讓結果更有說服力）。

| Update 情境 | 2PC Ops | 2PC Max Overhead | Subset Ops | Ops % | Subset Max Overhead |
|---|---|---|---|---|---|
| Hosts  | 239,830 | 92%  | 119,003 | 50% | 20% |
| Routes | 266,234 | 100% | 123,929 | 47% | 10% |
| Both   | 239,830 | 92%  | 142,379 | 59% | 20% |

**要重現的是「趨勢與比例」，不是精確數字**（論文沒有公開拓撲細節、亂數種子與 tie-breaking 規則，見 §2）。驗收標準見 §7。

---

## 2. 論文實驗設定拆解

### 2.1 論文有明確寫的

| 項目 | 論文內容（§8） |
|---|---|
| 規模 | 192 hosts、48 switches |
| 應用 | Routing：計算**每對 host 之間**的最短路徑 |
| 版本標記 | OpenFlow 1.0，用 **VLAN 欄位**存 version tag |
| 2PC 流程 | Phase 1 在內部埠裝新版規則（只匹配新版 tag）→ Phase 2 逐一更新 ingress 改蓋新 tag → 等封包排空後移除舊規則 |
| Subset | 找出被新增／移除／修改的路徑，只對這些路徑整條換新版本 |
| 情境 | 在 3 個 configuration 之間切換（C0→C1→C2） |
| Hosts 情境 | 隨機挑 10%–20% hosts 加入或移除 |
| Routes 情境 | 隨機挑 20% routes，假裝路徑上某台 switch 被移除而強制重新繞路 |
| Ops | OpenFlow **install** 訊息總數（以此代替時間，因 Mininet 無效能保真度） |
| Overhead | 每台 switch：更新期間「多出的規則數」÷ max(舊 config 規則數, 新 config 規則數)；Max Overhead 取全網最大值 |

### 2.2 論文沒寫、需要我們自訂的假設

| 未說明項目 | 我們的假設 | 理由 |
|---|---|---|
| 48 sw / 192 hosts 的 Fat Tree 長相 | **k=8 Fat Tree 取 4 個 pod**：16 core + 16 agg + 16 edge = 48 switches；每台 edge 掛 12 hosts = 192 hosts | 標準 k-ary fat tree 無法同時得到 48/192，這是能精確湊出兩個數字的最自然結構。另跑標準 k=4（20 sw / 16 hosts）作為開發與小規模驗證 |
| 規則粒度 | 每對 (src, dst) 一條路徑、路徑上每台 switch 一條規則 | 粗估：此拓撲完整 config ≈ 161k 條規則（每個 src：11×1 + 36×3 + 144×5 = 839 跳 × 192），install + delete 與論文 2.4×10⁵ 同數量級；若用 per-destination 規則只有 ~9k，對不上 |
| 最短路徑 tie-breaking | 固定規則（例如依 (src,dst) hash 在等價路徑中選一條，類 ECMP） | 會影響各 switch 負載，進而影響 Max Overhead；必須固定並記錄 |
| Ops 是否包含 delete | **兩者都報**：install-only 與 install+modify+delete | 論文只寫 "install operations"，但 2PC 數字推算看起來可能含刪除 |
| 3 個 config 的 Ops 是加總或平均 | 報「2 次更新加總」與「每次更新平均」 | 同上，論文未說明 |
| 亂數 | 每組情境跑 ≥10 個 seed，報 mean ± std | 降低單次抽樣偏差 |
| 排空時間 (drain) | 固定 D 秒（如 2 s），可設定 | 論文未給值；Mininet 中封包傳遞延遲遠小於此 |

> 原始 Kinetic（NOX 上的 Python 實作）目前沒有可直接取用的公開版本，因此改用 Ryu 重新實作。

---

## 3. 系統架構：兩層設計

Ops 與 Overhead 這兩個指標**只取決於「新舊 config + 更新順序」**，與封包實際怎麼跑無關。因此拆成兩層：

```
            ┌───────────────────────────────┐
            │  Model 層（純 Python，可測試）  │
            │  topo → routing → Config       │
            │  diff → UpdatePlan(op 序列)    │
            └──────────┬───────────┬────────┘
                       │           │
        ┌──────────────▼──┐   ┌────▼──────────────────────┐
        │ 離線模擬器        │   │ Ryu Controller             │
        │ 逐 op 計算每台    │   │ 依 UpdatePlan 下 FlowMod   │
        │ switch 規則數     │   │ + Barrier，於 Mininet 執行  │
        │ → Ops / Overhead │   │ → 實測流表大小 / 封包一致性 │
        └─────────────────┘   └────────────────────────────┘
                 └──────── 兩邊數字必須一致（交叉驗證）────────┘
```

- **離線模擬器**：秒級跑完全部 seed × 情境，產出 Table 2 的主要數字。
- **Mininet + Ryu**：證明這些規則真的能裝上 OVS、真的能轉送，並以實測流表數量驗證模擬器；同時驗證 per-packet consistency（更新期間零掉包、無迴圈）。
- 同一份 `UpdatePlan` 餵給兩邊，避免「模擬的跟實際跑的是不同東西」。

---

## 4. 實作細節

### 4.1 拓撲（`topo/fattree.py`）
- 單一來源產生 networkx Graph 與 Mininet `Topo`，確保兩邊一致。
- 參數：`k`、`pods`、`hosts_per_edge`；預設 paper-scale = (8, 4, 12)，dev-scale = (4, 4, 2)。
- 記錄每個 port 是 **ingress（接 host）** 還是 **internal（接 switch）**，2PC 需要這個分類。

### 4.2 Routing 應用（`model/routing.py`）
- 輸入：拓撲 + 在線 host 集合 + 被「禁用」的 switch 集合（Routes 情境用）。
- 輸出：`{(src, dst): [sw1, sw2, ...]}`，所有在線 host 有序對。
- Tie-breaking 依 §2.2 固定。

### 4.3 規則模型與版本標記（`model/config.py`）
OpenFlow 1.3（Ryu 支援較好），VLAN VID 作為 version tag。每條路徑 `[s1, …, sn]` 產生：

| 位置 | Match | Actions |
|---|---|---|
| Ingress（s1，從 host 埠進入） | in_port=host, eth_src, eth_dst, 無 VLAN | push VLAN, set vid=v, output |
| Internal（s2…s(n-1)） | vlan_vid=v, eth_src, eth_dst | output |
| Egress（sn） | vlan_vid=v, eth_src, eth_dst | pop VLAN, output host 埠 |
| 同一台 edge（n=1） | in_port=host, eth_src, eth_dst | output（不需 tag） |

`Config = {switch_id: frozenset[Rule]}`，immutable，方便做 diff。
所有規則帶 `cookie = version`，方便除錯與批次刪除。

### 4.4 更新機制（`model/update.py`）
輸出 `UpdatePlan = [Phase(ops=[FlowOp...]), Barrier, ...]`，`FlowOp ∈ {ADD, MODIFY, DELETE}`。

**2PC（baseline）**
1. Phase 1：對所有 switch 裝新版 (v+1) 的 internal / egress 規則 → Barrier
2. Phase 2：逐一 MODIFY ingress 規則改蓋 v+1（新出現的 pair 用 ADD；消失的 pair 不動）→ Barrier
3. 等待 drain 時間 D
4. Phase 3：DELETE 所有舊版 (v) internal / egress 規則，及已不存在 pair 的 ingress 規則 → Barrier

**Subset（對照組）**
1. diff 新舊路徑集：`added` / `removed` / `changed` / `unchanged`
2. `unchanged` 完全不動、維持舊版 tag
3. `added` + `changed`：沿整條新路徑裝新版規則 → 改 ingress → drain → 刪舊路徑規則
4. `removed`：先刪 ingress → drain → 刪 internal（pure retraction）
5. 純新增（pure extension）時退化為只裝新規則，與論文「extension 與 subset 成本相同」一致

### 4.5 實驗情境（`model/scenarios.py`）
每個 seed 產生 C0 → C1 → C2：

- **Hosts**：每步隨機抽 10%–20% hosts 切換上下線狀態（C0 為全部 192 台在線）
- **Routes**：hosts 固定；每步隨機抽 20% 現有 routes，從路徑中間（非兩端 edge）隨機挑一台 switch 視為移除，重算該 pair 的最短路徑
  - 注意：Fat Tree 有等長替代路徑，這正是論文 Routes 情境 2PC overhead ≈ 100% 的原因
- **Both**：同一步同時套用 Hosts 與 Routes

### 4.6 指標計算（`sim/metrics.py`）
- `ops_install`、`ops_total`：依 UpdatePlan 計數
- 逐 op 模擬每台 switch 的規則數，記錄峰值 `peak_s`
- `overhead_s = (peak_s − max(|old_s|, |new_s|)) / max(|old_s|, |new_s|)`（分母為 0 的 switch 略過）
- `max_overhead = max_s overhead_s`
- 一併輸出 overhead 分布（論文自己承認只報最大值偏悲觀，分布圖是加分項）

### 4.7 Mininet + Ryu 執行（`ctrl/update_app.py`、`exp/run_mininet.py`）
- Ryu app 讀入 UpdatePlan，依序送 FlowMod，每個 Phase 結束送 `OFPBarrierRequest` 並等回覆再進下一階段
- 控制器端計數實際送出的 FlowMod 數 → 必須等於模擬器的 Ops
- 每個 Barrier 後 + 每 100 ms 對所有 switch 發 `OFPAggregateStatsRequest` 取 flow_count → 實測 peak，對照模擬器
- **一致性檢查（加分）**：更新期間在抽樣的 host pair 間跑帶序號的 UDP 探測封包，統計掉包／重複；另跑一組「無版本、直接覆蓋規則」的 naive update 作對照，展示 2PC 的必要性

---

## 5. 專案目錄

```
Final Project/
├── reproduction-plan.md        # 本文件
├── src/
│   ├── topo/fattree.py
│   ├── model/{routing,config,update,scenarios}.py
│   ├── sim/{metrics,run_sim}.py
│   ├── ctrl/update_app.py      # Ryu app
│   └── exp/{run_mininet,probe}.py
├── tests/                      # pytest：拓撲數量、規則生成、diff、overhead 公式
├── results/                    # CSV / JSON，每筆含 seed 與參數
└── analysis/plot.py            # 產生表格與圖
```

---

## 6. 實驗矩陣與產出

| 層 | 拓撲 | 情境 | 策略 | Seeds |
|---|---|---|---|---|
| 離線 | paper-scale (48/192) | Hosts / Routes / Both | 2PC / Subset | 10 |
| 離線 | dev-scale (20/16) | 同上 | 同上 | 10 |
| Mininet | dev-scale | 同上 | 同上 | 3（交叉驗證） |
| Mininet | paper-scale | Routes（overhead 最高） | 2PC / Subset | 1–3 |

**產出**
1. 重現版 Table 2（Fat Tree × Routing 三列），與論文數字並列
2. 每台 switch 的 overhead 分布圖（箱型圖）
3. 模擬器 vs. Mininet 實測的對照表（應完全一致）
4. 一致性檢查結果：2PC vs. naive 的掉包數

---

## 7. 驗收標準

| 指標 | 期望 | 依據 |
|---|---|---|
| 2PC Max Overhead | Routes ≈ 100%；Hosts / Both 略低於 100%（約 85–95%） | 新舊 config 同時存在；Hosts 情境新 config 可能較小 |
| Subset Ops % | 約 40%–60% | 論文 47%–59% |
| Subset Max Overhead | 明顯低於 2PC（約 10%–30%） | 論文 10%–20% |
| 2PC Ops 數量級 | 10⁵ | 論文 2.4–2.7×10⁵ |
| 模擬 vs. Mininet | Ops 與 peak 規則數完全相同 | 同一份 UpdatePlan |
| 一致性 | 2PC 更新期間掉包 = 0 | Theorem 6 |

若某指標偏離，先檢查 §2.2 的假設（tie-breaking、Ops 是否含 delete），並在報告中說明差異來源。

---

## 8. 時程與分工（對齊選題報告）

| 時間 | 負責 | 工作 | 完成標準 |
|---|---|---|---|
| 11/25–11/30 | 王屹閎 | 環境建置；拓撲、routing、Config、2PC UpdatePlan；離線模擬器 | 離線跑出 2PC 三種情境的 Ops / Max Overhead |
| 12/01–12/07 | 王屹閎 | Ryu app 執行 UpdatePlan；dev-scale Mininet 交叉驗證 | Baseline 完成：模擬 = 實測 |
| 12/01–12/07 | 陳元挺 | Subset 機制；paper-scale Mininet 跑通 | 重現版 Table 2 初稿 |
| 12/08–12/15 | 陳元挺 | Timeout 延伸實驗（見 §9.3） | overhead-over-time 對比圖 |
| 12/16–期末 | 共同 | 自動化量測腳本、多 seed、繪圖、一致性檢查、報告 | 最終表格與圖 |

---

## 9. 風險與對策

### 9.1 環境
| 風險 | 對策 |
|---|---|
| WSL2 預設 kernel 可能沒有 Open vSwitch kernel module，Mininet 無法用 kernel datapath | 優先用 VirtualBox/VMware 跑 Ubuntu 22.04（或官方 Mininet 2.3 VM）；若堅持 WSL2，改用 OVS userspace datapath 或自編 kernel |
| Ryu 已停止維護，在 Python ≥ 3.10 有 eventlet 相容問題（本機 WSL 為 Ubuntu 26.04，系統 Python 過新） | 用 Python 3.8/3.9 venv（pyenv）並固定 `eventlet==0.30.2`；或改用 OpenStack 維護的 fork **os-ken**（API 幾乎相同） |
| paper-scale 約 16 萬條規則、2PC 峰值約 32 萬，Ryu 下發較慢 | 離線模擬器提供主要數字；Mininet paper-scale 只跑少量 seed；FlowMod 批次送出、每 phase 才 Barrier |

### 9.2 方法
| 風險 | 對策 |
|---|---|
| 論文拓撲／Ops 定義不明，數字對不上 | §2.2 明列假設、兩種 Ops 都報；以比例與趨勢為驗收依據 |
| tie-breaking 不同導致 Max Overhead 偏差 | 固定並在報告中說明；可額外跑「隨機 tie-breaking」作敏感度分析 |

### 9.3 Timeout 延伸（選題報告 p.6）需要先釐清的設計問題
這部分不屬於論文重現，但動手前建議先處理以下三點，否則對比可能不公平：

1. **舊規則的 timeout 何時設定？** OpenFlow 的 `MODIFY` 不會更新既有規則的 timeout；若要在 Phase 1 才讓舊規則帶 timeout，必須用相同 match/priority 重新 `ADD` 覆蓋，這本身就是每條規則一則訊息，**控制器訊息數不會比刪除少**。若從一開始就裝 `idle_timeout`，沒有流量的 host pair 在穩態下規則也會過期，破壞轉送正確性。
2. **Baseline 刪除其實很便宜**：舊規則都帶 `cookie=v` 或 `vlan_vid=v`，Phase 3 可用每台 switch **一則**帶 cookie mask 的 `DELETE` 清掉（48 則訊息）。若 baseline 採逐條刪除，對比會被誇大。
3. **「Overhead 長期維持 100%」的前提**：baseline 在 drain 時間 D 後就會刪除舊規則，overhead 只持續 D；timeout 版本則持續約 idle_timeout T。兩者的差異取決於 D 與 T 的取值。

建議的公平比較指標：**overhead 隨時間的曲線、overhead × 時間的積分、控制器訊息數（baseline 分別以逐條刪除與 cookie 批次刪除兩種版本比較）**，並在相同 D = T 條件下對比。

---

## 10. 下一步

1. 建好 Linux 實驗環境（VM + Mininet 2.3 + Ryu/os-ken），跑通 `ryu.app.simple_switch_13` + `mn --topo tree`
2. 先寫 `tests/`：拓撲數量（48/192、20/16）、規則生成、overhead 公式的手算案例
3. 實作 Model 層與離線模擬器 → 第一版 2PC 數字
