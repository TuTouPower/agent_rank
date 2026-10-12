# 单价数据更新指南

本仓单价来自公开定价仓 [`FeiZhuLulu/real-api-pricing`](https://github.com/FeiZhuLulu/real-api-pricing) 的采用表 `data/adopted.csv`。更新入口是单一脚本 `scripts/update_pricing.py`：抓最新 → 按模型表对齐 → 应用硬编码覆盖 → 写出产物。不另建「转换配置」文件。

______________________________________________________________________

## 1. 运行方式

在仓库根目录：

```bash
# 默认：HTTPS 拉取定价仓 main 最新 adopted.csv（缓存到 .scratch/pricing_cache/）
python3 scripts/update_pricing.py

# 离线 / 测试：指向本地 CSV
python3 scripts/update_pricing.py --csv /path/to/adopted.csv

# 等价环境变量（CLI --csv 优先）
AGENT_RANK_ADOPTED_CSV=/path/to/adopted.csv python3 scripts/update_pricing.py
```

写出：

- 成功：覆盖 `data/latest_pricing.json`（须等于采用表行数）与空的 `data/unmatched_pricing.json`
- 失败（有未对齐）：**不**覆盖 `latest_pricing.json`；写出 `unmatched_pricing.json` 诊断清单；进程非零退出

模型表路径默认 `data/models.json`，可用 `--models` 覆盖。

______________________________________________________________________

## 2. 对齐规则

对采用表每一行取 `served_model`：

1. 若等于模型表某行 `id`，或落在该行 `pricing_aliases` 中 → 写入单价表，`model` 为本仓 `id`。
2. 否则写入未对齐清单（含套餐名、上游 `served_model`、原因）。

**精确字符串匹配**；禁止大小写折叠、禁止猜测。CSV 用 `utf-8-sig` 读取（`plan_id` 可能带 BOM）。

套餐 → 本仓 `source` 由脚本内 `PLAN_TO_SOURCE` 常量对照；对不上则为空字符串 `""`。至少包含 `OpenCode Go` → `opencode-go`。

置信度 `confidence`、出处 `citation`（上游 `source` 列）原样保留自上游。

______________________________________________________________________

## 3. 本仓覆盖规则

改动会写入该行 `notes`（接在上游 `decision_note` 后）：

1. **OpenCode Go × deepseek-v4.1-flash**（本仓 id `deepseek-v4.1-flash`）：官网单模型月额度 $60→$80；`monthly_tokens` / `monthly_yi` 按 `80/60` 放大；`price_usd` 不变；`real_usd_per_mtok = price_usd / (monthly_tokens / 1e6)`。其余 DeepSeek 行（V4 Flash $30、V4 Pro $15、V4 Flash Vision Exp $15）直接用上游值，不再整体放大。
2. **Command Code GOAT**：`price_usd` 从 10 改为 10.78；月额度不变；仅按新月费重算 `real_usd_per_mtok`。

______________________________________________________________________

## 4. 未对齐时如何补录（必须拿全）

脚本要求采用表每一行都能对齐；对不上就报错退出，禁止留下残缺单价表。

1. 看终端 ERROR 与 `data/unmatched_pricing.json` 里的 `served_model`。
2. 若对应本仓已有模型但字面不同：在 `data/models.json` 该行加 `pricing_aliases`。
3. 若本仓尚无该模型：增模型表行（可用上游 `served_model` 作为 `id`），必要时再加别名。
4. 再跑 `python3 scripts/update_pricing.py`，确认退出码 0 且 `unmatched_pricing.json` 为 `[]`。

______________________________________________________________________

## 5. 与测速流水线的关系

本脚本**不**改 `run_bench.py` / `report.py` / `data/results.jsonl` / `data/latest_*.json` 行为。单价产物独立入库，供后续排名维度使用。
