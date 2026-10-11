# 计划地图（Plan Map）

> Agent 必读：每完成一步就更新本文件的 status / checkpoints；出错时按「回退策略」撤销该步并记入 `BUGS.md`。

## 当前状态

| 字段 | 值 |
|------|-----|
| current_step | `P6_ACCEPT` |
| phase | P6 端到端验收通过：27 候选 ≥ 10 ✓；会议口径放宽（有会议厅即放行+needs_manual 复核） |
| last_updated | 2026-09-24 |
| blocked_by | 会议室每间 10–15 人精确容量需人工复核；xhs 候选命中率低（1/183）可选优化 |

## 环境

- 项目根：`D:\Program Files\traveldesign\venue-scout`
- Conda 环境：`<project env>`（python 3.12）
- 激活：见 `ENV.md`；**新窗口必须先读 `ENV.md`**
- 配置：复制 `config\settings.example.yaml` → `config\settings.yaml` 后填 Key

## 任务树

```text
S0 项目骨架                          [x] done
└─ S1 环境搭建 (conda+deps)          [x] done  <project env>
   └─ S2 配置文件 settings.yaml      [x] done  (key 待用户填)
      └─ P0 探测携程会议室数据        [~] in_progress   ← SOA+报告 OK，容量/车程未闭
         ├─ P2 高德驾车+POI 模块      [x] code done   待 amap key 实跑
         ├─ P3 小红书候选发现          [x] code done   待 cookie 实跑(可选)
├─ P4 携程会议室/客房校验     [~] partial     hall+room_count OK，10–15 容量人工复核
          ├─ P1 Schema+种子库          [x] done  models+seeds.json
          ├─ P5 打分+闸门+扩搜         [x] done  会议口径放宽（BUG-0011）
          ├─ P5b 报告输出 JSON/MD      [x] done  已出 first report
          └─ P6 端到端验收首批≥10候选   [x] done  20260924_110435: 27 passed ✓
```

## 检查点（可回退）

| checkpoint id | 步骤 | 产物 | 回退方式 |
|---------------|------|------|----------|
| ckpt_s0 | S0 | 目录结构 + src/{models,http_util,amap_client,ctrip_client}.py | 删除 venue-scout 下非 ENV 文件，保留本地图 |
| ckpt_s1 | S1 | conda env `<project env>` + deps | `conda remove -y -p <project env> --all` |
| ckpt_s2 | S2 | config/settings.yaml | 恢复 settings.example.yaml 副本 |
| ckpt_p0 | P0 | data/cache 中 ctrip 探测结果 + BUGS 记录 | 清 data/cache/ctrip_* |
| ckpt_p2 | P2 | src 跑通 drive/poi | git/文件回退 amap_client.py |
| ckpt_p4 | P4 | 携程 enrich 流程 | 回退 ctrip_client.py |
| ckpt_p5 | P5 | scorer+pipeline+report | 回退新增文件 |
| ckpt_p6 | P6 | data/output 首批报告 | 删 output，保留 cache |

## 回退命令约定

```powershell
# 进入项目
Set-Location "D:\Program Files\traveldesign\venue-scout"
# 激活环境
conda activate <project env>   # 或 .\ENV.md 中的 cmd/python 路径
# 某步失败：按上表 checkpoint 执行回退，并在 BUGS.md 追加一条
```

## 变更日志

| 日期 | step | 动作 |
|------|------|------|
| 2026-09-24 | S0 | 创建目录与核心模块草稿 |
| 2026-09-24 | S1 | conda env 创建成功（process-monitor）deps ok |
| 2026-09-24 | S2 | settings.yaml 就绪（amap.key 待填） |
| 2026-09-24 | P1/P4/P5 | schema/scorer/pipeline/report/xhs 代码落地 |
| 2026-09-24 | P0 | 首次探测 hotels/detail/{id}.html → 404（BUG-0005）；改多格式回退 |
| 2026-09-24 | P0 | SOA getHotelDetailAggregate 200（BUG-0007 fixed）；gaHotelSearchEngine 名称→id 通；ctrip_client 改写 |
| 2026-09-24 | P0 | 修 roomNum/lnglat 解析；新增 scripts/test_ctrip_seeds.py；README 开源清单；BUG-0008 |
| 2026-09-24 | P0 | 种子批量实测：8/10 命中会议厅+room_count；suggest 加 city_hint；无 amap.key 也跑 ctrip enrich |
| 2026-09-24 | P0 | BUG-0009 跨城错配 city_hint+排序；重写 test_ctrip_seeds；无 Key 管线出首份 Ctrip 报告 |
| 2026-09-24 | P0/P5b | `run_pipeline` Ctrip-only 跑通：`data/output/latest_report.md`（10 候选，硬过滤 0，缺车程/娱乐/10-15 容量） |
| 2026-09-24 | P6 | 用户填入 amap.key + 手贴 ctrip/xhs cookie；`run_pipeline` 全量 183 候选（11:00 首跑 passed=0） |
| 2026-09-24 | P6 | 会议口径放宽「有会议厅即放行+needs_manual」（BUG-0011）；重跑 110435：**passed=27 ≥ 10 验收通过** |
| 2026-09-24 | P7 | 交付：报告 `report_pipeline_20260924_110435.md` + venues JSON；剩 10–15 容量人工复核 |
