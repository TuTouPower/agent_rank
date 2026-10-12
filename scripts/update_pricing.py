#!/usr/bin/env python3
"""update_pricing.py — 抓取定价仓 adopted.csv，对齐本仓模型表并写出单价产物。

默认从 FeiZhuLulu/real-api-pricing 的 main 分支拉取最新 `data/adopted.csv`
（HTTPS raw）。测试或离线可用环境变量 `AGENT_RANK_ADOPTED_CSV` 或 CLI
`--csv` 指向本地文件。覆盖规则硬编码在本脚本，不另建转换配置。

写出：
- data/latest_pricing.json（对齐成功的单价行；须覆盖采用表全部行）
- data/unmatched_pricing.json（仅在失败时写出诊断清单）

任一上游行无法在模型表对齐时：打印报错、写出未对齐清单、**不**覆盖
latest_pricing.json，并以非零退出码失败。必须拿全数据。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ADOPTED_URL = (
    "https://raw.githubusercontent.com/FeiZhuLulu/real-api-pricing/"
    "main/data/adopted.csv"
)

# 套餐名 → 本仓测速 source；对不上则为 ""（空字符串）。
PLAN_TO_SOURCE: dict[str, str] = {
    "OpenCode Go": "opencode-go",
    "DeepSeek V4 Flash API 闲时": "deepseek-official",
    "DeepSeek V4 Flash API 忙时": "deepseek-official",
    "DeepSeek V4 Pro API 闲时": "deepseek-official",
    "DeepSeek V4 Pro API 忙时": "deepseek-official",
    "DeepSeek V4.1 Flash API 闲时": "deepseek-official",
    "DeepSeek V4.1 Flash API 忙时": "deepseek-official",
    "MiMo V2.6 Flash API": "mimo-official",
    "MiMo V2.6 Pro API": "mimo-official",
    "MiMo V2.6 Pro UltraSpeed API": "mimo-official",
    "MiMo Token Plan Lite 日间": "mimo-official",
    "MiMo Token Plan Lite 夜间0.8×": "mimo-official",
    "MiMo Token Plan Standard 日间": "mimo-official",
    "MiMo Token Plan Standard 夜间0.8×": "mimo-official",
    "MiMo Token Plan Pro 日间": "mimo-official",
    "MiMo Token Plan Pro 夜间0.8×": "mimo-official",
    "MiMo Token Plan Max 日间": "mimo-official",
    "MiMo Token Plan Max 夜间0.8×": "mimo-official",
    "MiniMax Token Plan Plus": "minimax-official",
    "MiniMax Token Plan Plus (Global)": "minimax-official",
    "MiniMax Token Plan Max": "minimax-official",
    "MiniMax Token Plan Max (Global)": "minimax-official",
    "MiniMax Token Plan Ultra": "minimax-official",
    "MiniMax Token Plan Ultra (Global)": "minimax-official",
    "MiniMax M Plan Go (CN)": "minimax-official",
    "MiniMax M Plan Go (CN Annual)": "minimax-official",
    "MiniMax M Plan Explore (CN)": "minimax-official",
    "MiniMax M Plan Explore (CN Annual)": "minimax-official",
    "MiniMax M Plan Build (CN)": "minimax-official",
    "MiniMax M Plan Build (CN Annual)": "minimax-official",
    "SuperGrok": "grok-build",
    "SuperGrok Lite": "grok-build",
    "SuperGrok Plus": "grok-build",
    "SuperGrok Heavy": "grok-build",
    "Grok 4.6 API (<200k)": "grok-build",
    "Grok 4.7 API (<200k)": "grok-build",
    "ChatGPT Plus": "codex",
    "ChatGPT Pro 5x": "codex",
    "ChatGPT Pro 20x": "codex",
    "GPT-5.6 Luna API": "codex",
    "GPT-5.6 Sol API": "codex",
    "GPT-5.6 Terra API": "codex",
    "Kimi 会员 49": "kimi-code",
    "Kimi 会员 99": "kimi-code",
    "Kimi 会员 199": "kimi-code",
    "Kimi 会员 699": "kimi-code",
    "Google AI Pro": "antigravity",
    "Google AI Ultra 5x": "antigravity",
    "Google AI Ultra 20x": "antigravity",
}

OPENCODE_GO_PLAN = "OpenCode Go"
OPENCODE_DEEPSEEK_MODEL = "deepseek-v4.1-flash"
OPENCODE_DEEPSEEK_USAGE_FROM = 60.0
OPENCODE_DEEPSEEK_USAGE_TO = 80.0
COMMAND_CODE_GOAT_PLAN = "Command Code GOAT"
COMMAND_CODE_GOAT_PRICE = 10.78

PRICING_FIELDS = (
    "plan",
    "model",
    "source",
    "billing",
    "price_usd",
    "monthly_tokens",
    "monthly_yi",
    "real_usd_per_mtok",
    "unmetered",
    "promo_until",
    "confidence",
    "citation",
    "notes",
)


def _parse_optional_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return float(s)


def _parse_optional_int(raw: str | None) -> int | None:
    f = _parse_optional_float(raw)
    if f is None:
        return None
    return int(f)


def _parse_unmetered(raw: str | None) -> bool | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "yes", "y"):
        return True
    if s in ("0", "false", "no", "n"):
        return False
    return None


def load_models(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"models.json 须为数组: {path}")
    return data


def build_served_index(models: list[dict[str, Any]]) -> dict[str, str]:
    """served_model / id → 本仓 id。精确匹配，无大小写折叠。"""
    index: dict[str, str] = {}
    for row in models:
        mid = row["id"]
        index[mid] = mid
        for alias in row.get("pricing_aliases") or []:
            if alias in index and index[alias] != mid:
                raise ValueError(f"pricing_aliases 冲突: {alias!r}")
            index[alias] = mid
    return index


def fetch_adopted_csv(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "agent_rank/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())
    return dest


def resolve_csv_path(cli_csv: str | None, cache_dir: Path) -> Path:
    if cli_csv:
        return Path(cli_csv)
    env = os.environ.get("AGENT_RANK_ADOPTED_CSV", "").strip()
    if env:
        return Path(env)
    cache = cache_dir / "adopted.csv"
    fetch_adopted_csv(DEFAULT_ADOPTED_URL, cache)
    return cache


def read_adopted_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def recompute_real_usd_per_mtok(price_usd: float | None, monthly_tokens: int | None) -> float | None:
    if price_usd is None or monthly_tokens is None or monthly_tokens <= 0:
        return None
    return price_usd / (monthly_tokens / 1_000_000)


def apply_overrides(
    plan: str,
    local_model_id: str,
    price_usd: float | None,
    monthly_tokens: int | None,
    monthly_yi: float | None,
    real_usd_per_mtok: float | None,
    notes: str,
) -> tuple[float | None, int | None, float | None, float | None, str]:
    """返回 (price_usd, monthly_tokens, monthly_yi, real_usd_per_mtok, notes)。"""
    extra: list[str] = []

    if plan == OPENCODE_GO_PLAN and local_model_id == OPENCODE_DEEPSEEK_MODEL:
        scale = OPENCODE_DEEPSEEK_USAGE_TO / OPENCODE_DEEPSEEK_USAGE_FROM
        if monthly_tokens is not None:
            monthly_tokens = int(round(monthly_tokens * scale))
        if monthly_yi is not None:
            monthly_yi = monthly_yi * scale
        real_usd_per_mtok = recompute_real_usd_per_mtok(price_usd, monthly_tokens)
        extra.append(
            f"本仓覆盖：OpenCode Go {OPENCODE_DEEPSEEK_MODEL} 单模型月额度 "
            f"${OPENCODE_DEEPSEEK_USAGE_FROM:g}→${OPENCODE_DEEPSEEK_USAGE_TO:g}，"
            f"额度 ×{scale:g}，price_usd 不变，按 price_usd/(monthly_tokens/1e6) 重算真实单价。"
        )

    if plan == COMMAND_CODE_GOAT_PLAN:
        price_usd = COMMAND_CODE_GOAT_PRICE
        real_usd_per_mtok = recompute_real_usd_per_mtok(price_usd, monthly_tokens)
        extra.append(
            f"本仓覆盖：Command Code GOAT 月费 10→{COMMAND_CODE_GOAT_PRICE}，"
            "月额度不变，按新月费重算真实单价。"
        )

    if extra:
        joined = " ".join(extra)
        notes = f"{notes} | {joined}" if notes else joined

    return price_usd, monthly_tokens, monthly_yi, real_usd_per_mtok, notes


def row_to_pricing(
    upstream: dict[str, str],
    local_model_id: str,
) -> dict[str, Any]:
    plan = (upstream.get("plan_name") or "").strip()
    price_usd = _parse_optional_float(upstream.get("price_usd"))
    monthly_tokens = _parse_optional_int(upstream.get("monthly_tokens"))
    monthly_yi = _parse_optional_float(upstream.get("monthly_yi"))
    real = _parse_optional_float(upstream.get("real_usd_per_mtok"))
    notes = (upstream.get("decision_note") or "").strip()

    price_usd, monthly_tokens, monthly_yi, real, notes = apply_overrides(
        plan, local_model_id, price_usd, monthly_tokens, monthly_yi, real, notes
    )

    return {
        "plan": plan,
        "model": local_model_id,
        "source": PLAN_TO_SOURCE.get(plan, ""),
        "billing": (upstream.get("billing") or "").strip() or None,
        "price_usd": price_usd,
        "monthly_tokens": monthly_tokens,
        "monthly_yi": monthly_yi,
        "real_usd_per_mtok": real,
        "unmetered": _parse_unmetered(upstream.get("unmetered")),
        "promo_until": (upstream.get("promo_until") or "").strip() or None,
        "confidence": (upstream.get("confidence") or "").strip() or None,
        "citation": (upstream.get("source") or "").strip() or None,
        "notes": notes or None,
    }


def build_pricing(
    models: list[dict[str, Any]],
    adopted_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    index = build_served_index(models)
    latest: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for upstream in adopted_rows:
        served = (upstream.get("served_model") or "").strip()
        plan = (upstream.get("plan_name") or "").strip()
        if not served:
            unmatched.append(
                {
                    "plan": plan,
                    "served_model": served,
                    "reason": "missing_served_model",
                }
            )
            continue
        local_id = index.get(served)
        if local_id is None:
            unmatched.append(
                {
                    "plan": plan,
                    "served_model": served,
                    "reason": "no_exact_id_or_alias",
                }
            )
            continue
        latest.append(row_to_pricing(upstream, local_id))

    latest.sort(
        key=lambda r: (
            float("inf") if r.get("real_usd_per_mtok") is None else r["real_usd_per_mtok"],
            r.get("plan") or "",
            r.get("model") or "",
        )
    )
    return latest, unmatched


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="更新本仓单价产物（对齐模型表 + 业务覆盖）")
    ap.add_argument(
        "--csv",
        help="本地 adopted.csv（优先于环境变量与网络拉取）",
    )
    ap.add_argument(
        "--models",
        default=str(REPO_ROOT / "data" / "models.json"),
        help="本仓模型表路径",
    )
    ap.add_argument(
        "--out-latest",
        default=str(REPO_ROOT / "data" / "latest_pricing.json"),
        help="对齐单价输出路径",
    )
    ap.add_argument(
        "--out-unmatched",
        default=str(REPO_ROOT / "data" / "unmatched_pricing.json"),
        help="未对齐清单输出路径",
    )
    ap.add_argument(
        "--cache-dir",
        default=str(REPO_ROOT / ".scratch" / "pricing_cache"),
        help="网络抓取缓存目录",
    )
    args = ap.parse_args(argv)

    csv_path = resolve_csv_path(args.csv, Path(args.cache_dir))
    models = load_models(Path(args.models))
    adopted = read_adopted_rows(csv_path)
    latest, unmatched = build_pricing(models, adopted)

    if unmatched:
        write_json(Path(args.out_unmatched), unmatched)
        # 禁止写出残缺单价表：不覆盖既有 latest_pricing.json
        samples = unmatched[:20]
        lines = [
            f"ERROR: 单价对齐未拿全数据：采用表 {len(adopted)} 行，对齐 {len(latest)} 行，"
            f"未对齐 {len(unmatched)} 行。请先补 data/models.json（id 或 pricing_aliases），再重跑。",
            f"未对齐清单已写入: {args.out_unmatched}",
            "示例:",
        ]
        for u in samples:
            lines.append(
                f"  - plan={u.get('plan')!r} served_model={u.get('served_model')!r} "
                f"reason={u.get('reason')!r}"
            )
        if len(unmatched) > len(samples):
            lines.append(f"  … 另有 {len(unmatched) - len(samples)} 行，见清单文件")
        print("\n".join(lines), flush=True)
        return 1

    write_json(Path(args.out_latest), latest)
    write_json(Path(args.out_unmatched), unmatched)  # 空数组，表示全量对齐
    print(
        f"wrote {args.out_latest} ({len(latest)} rows), "
        f"{args.out_unmatched} ({len(unmatched)} rows) from {csv_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
