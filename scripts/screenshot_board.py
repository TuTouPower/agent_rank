#!/usr/bin/env python3
"""screenshot_board.py — 渲染并截取 Agent Rank 榜单预览图（200K 测速榜与单价榜）。

采用轻量独立 HTML 模板 + Chrome Headless (Retina @2x) + PIL 自动裁边。
仅截取榜单卡片及其周围区域，不包含下方实验说明等无关内容。

输出两张图：
1. 200K 测速场景：包含序号、模型名字、端到端速度、生成速度。
2. 单价场景：包含序号、模型名字、有效单价、套餐。

用法：
  python3 scripts/screenshot_board.py
  python3 scripts/screenshot_board.py --limit-pricing 30
  python3 scripts/screenshot_board.py --out-speed docs/board-200k-speed.png --out-pricing docs/board-pricing.png
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LATEST_200K = REPO_ROOT / "data" / "latest_200k.json"
DEFAULT_LATEST_PRICING = REPO_ROOT / "data" / "latest_pricing.json"
DEFAULT_OUT_SPEED = REPO_ROOT / "docs" / "board-200k-speed.png"
DEFAULT_OUT_PRICING = REPO_ROOT / "docs" / "board-pricing.png"
DEFAULT_OUT_LEGACY = REPO_ROOT / "docs" / "board-preview-dark.png"

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome-stable") or "",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if c and Path(c).is_file():
            return c
    raise RuntimeError("未找到 Chrome / Chromium，无法截图。可设置 CHROME_BIN 环境变量。")


def crop_whitespace(src: Path, dest: Path, pad: int = 24) -> None:
    img = Image.open(src)
    bg = Image.new(img.mode, img.size, img.getpixel((0, 0)))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if bbox:
        cropped = img.crop(
            (
                max(0, bbox[0] - pad),
                max(0, bbox[1] - pad),
                min(img.width, bbox[2] + pad),
                min(img.height, bbox[3] + pad),
            )
        )
        cropped.save(dest, "PNG")
    else:
        img.save(dest, "PNG")


def fmt_usd_per_mtok(n: float | None) -> str:
    if n is None:
        return "—"
    v = float(n) * 1000
    if v == 0:
        return "$0"
    if v < 1e-4:
        return f"${v:.2e}"
    if v < 0.01:
        return f"${v:.6f}".rstrip("0").rstrip(".")
    if v < 1:
        return f"${v:.4f}".rstrip("0").rstrip(".")
    if v < 100:
        return f"${v:.2f}"
    return f"${v:.2f}"


BASE_CSS = """
:root {
  --bg: #0a0c10;
  --panel: #0e1218;
  --panel2: #141a23;
  --line: #1c2330;
  --line2: #2a3345;
  --text: #e8edf5;
  --muted: #8b96a8;
  --muted2: #556173;
  --accent: #5e8bff;
  --accent-text: #7ba1ff;
  --accent-soft: rgba(94, 139, 255, 0.12);
  --good: #34d399;
  --warn: #fbbf24;
  --dim: #5c6675;
  --row-head: #0c1016;
  --row-hover: rgba(94, 139, 255, 0.05);
  --row-line: #161d29;
  --track: #1a2230;
  --bar-from: #5e8bff;
  --bar-to: #38e0ff;
  --mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Consolas, Menlo, monospace;
  --sans: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  padding: 32px;
  display: flex;
  justify-content: center;
}
.board-frame {
  position: relative;
  width: 960px;
  background: var(--panel);
  border: 1px solid var(--line2);
  border-radius: 2px;
}
.x-corner {
  position: absolute;
  font: 400 13px/1 var(--mono);
  color: var(--accent-text);
  pointer-events: none;
}
.x-corner.tl { top: -7px; left: -5px; }
.x-corner.tr { top: -7px; right: -5px; }
.x-corner.bl { bottom: -7px; left: -5px; }
.x-corner.br { bottom: -7px; right: -5px; }

.scenario-bar {
  display: flex;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
.scenario-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px 16px;
  font: 600 11px/1.4 var(--mono);
  letter-spacing: .1em;
  color: var(--muted);
  border-right: 1px solid var(--line);
  text-transform: uppercase;
}
.scenario-btn:last-child { border-right: 0; }
.scenario-btn.active {
  color: var(--accent-text);
  background: var(--accent-soft);
}

.table {
  width: 100%;
  display: grid;
  padding: 0 20px;
}
.table.speed {
  grid-template-columns: 50px 1fr 180px 180px;
}
.table.pricing {
  grid-template-columns: 50px 1fr 180px 240px;
}

.tr {
  display: contents;
}
.tr > * {
  min-height: 50px;
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--row-line);
  padding: 8px 12px;
}
.tr.head > * {
  background: var(--row-head);
  min-height: 42px;
  border-bottom: 1px solid var(--line2);
  font: 600 10.5px/1.2 var(--mono);
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted2);
}
.th-rank { justify-content: flex-end; }
.th-num { justify-content: flex-end; text-align: right; }

.rank {
  justify-content: flex-end;
  font: 600 11.5px/1 var(--mono);
  color: var(--muted2);
  letter-spacing: .05em;
}
.rank.top { color: var(--accent-text); font-weight: 700; }

.model-name {
  font-weight: 650;
  font-size: 13.5px;
  color: var(--text);
  letter-spacing: -.15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.speed-cell {
  flex-direction: column;
  align-items: stretch;
  justify-content: center;
}
.speed-cell .metric {
  align-self: flex-end;
}
.metric {
  font: 600 13px/1.2 var(--mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -.2px;
  color: var(--text);
}
.metric.top { color: var(--good); }
.metric small {
  font-size: 9.5px;
  color: var(--muted2);
  font-weight: 500;
  margin-left: 3px;
}
.speed-track {
  width: 100%;
  height: 3px;
  background: var(--track);
  margin-top: 6px;
  overflow: hidden;
}
.speed-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--bar-from), var(--bar-to));
  opacity: .85;
}

.plan-name {
  font: 500 12px/1.2 var(--mono);
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
"""


def render_speed_html(rows: list[dict[str, Any]]) -> str:
    max_e2e = max((r.get("e2e_tps") or 0) for r in rows) or 1.0
    max_gen = max((r.get("gen_tps") or 0) for r in rows) or 1.0

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{BASE_CSS}</style></head>
<body>
<div class="board-frame">
  <i class="x-corner tl">+</i><i class="x-corner tr">+</i>
  <i class="x-corner bl">+</i><i class="x-corner br">+</i>
  <div class="scenario-bar">
    <div class="scenario-btn">一句话测速</div>
    <div class="scenario-btn">10K 测速</div>
    <div class="scenario-btn active">200K 测速</div>
    <div class="scenario-btn">单价</div>
  </div>
  <div class="table speed">
    <div class="tr head">
      <div class="th-rank">#</div>
      <div>模型</div>
      <div class="th-num">端到端速度</div>
      <div class="th-num">生成速度</div>
    </div>
"""
    for idx, r in enumerate(rows, 1):
        rank_cls = "top" if idx <= 3 else ""
        e2e = r.get("e2e_tps")
        gen = r.get("gen_tps")
        e2e_str = f"{e2e:.1f}<small>tok/s</small>" if e2e is not None else "—"
        gen_str = f"{gen:.1f}<small>tok/s</small>" if gen is not None else "—"
        e2e_w = (e2e / max_e2e * 100) if e2e else 0.0
        gen_w = (gen / max_gen * 100) if gen else 0.0
        html += f"""
    <div class="tr">
      <div class="rank {rank_cls}">{idx:02d}</div>
      <div class="model-name">{r.get('model', '—')}</div>
      <div class="speed-cell">
        <div class="metric {rank_cls}">{e2e_str}</div>
        <div class="speed-track"><div class="speed-fill" style="width:{e2e_w:.1f}%"></div></div>
      </div>
      <div class="speed-cell">
        <div class="metric">{gen_str}</div>
        <div class="speed-track"><div class="speed-fill" style="width:{gen_w:.1f}%"></div></div>
      </div>
    </div>"""

    html += """
  </div>
</div>
</body></html>"""
    return html


def render_pricing_html(rows: list[dict[str, Any]]) -> str:
    priced = [r.get("real_usd_per_mtok") for r in rows if r.get("real_usd_per_mtok") is not None]
    max_p = max(priced) if priced else 1.0

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{BASE_CSS}</style></head>
<body>
<div class="board-frame">
  <i class="x-corner tl">+</i><i class="x-corner tr">+</i>
  <i class="x-corner bl">+</i><i class="x-corner br">+</i>
  <div class="scenario-bar">
    <div class="scenario-btn">一句话测速</div>
    <div class="scenario-btn">10K 测速</div>
    <div class="scenario-btn">200K 测速</div>
    <div class="scenario-btn active">单价</div>
  </div>
  <div class="table pricing">
    <div class="tr head">
      <div class="th-rank">#</div>
      <div>模型</div>
      <div class="th-num">有效单价</div>
      <div>套餐</div>
    </div>
"""
    for idx, r in enumerate(rows, 1):
        rank_cls = "top" if idx <= 3 else ""
        p = r.get("real_usd_per_mtok")
        p_str = fmt_usd_per_mtok(p)
        if p_str != "—":
            p_str = f"{p_str}<small>/BTok</small>"
        p_w = ((max_p - p) / max_p * 100) if (p is not None and max_p > 0) else 0.0
        html += f"""
    <div class="tr">
      <div class="rank {rank_cls}">{idx:02d}</div>
      <div class="model-name">{r.get('model', '—')}</div>
      <div class="speed-cell">
        <div class="metric {rank_cls}">{p_str}</div>
        <div class="speed-track"><div class="speed-fill" style="width:{p_w:.1f}%"></div></div>
      </div>
      <div class="plan-name">{r.get('plan', '—')}</div>
    </div>"""

    html += """
  </div>
</div>
</body></html>"""
    return html


def chrome_render_to_png(chrome_bin: str, html_content: str, dest_png: Path, width: int = 1050, height: int = 2200, scale: int = 2) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as f:
        f.write(html_content)
        tmp_html = Path(f.name)

    raw_png = tmp_html.with_suffix(".raw.png")
    try:
        cmd = [
            chrome_bin,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",
            f"--window-size={width},{height}",
            f"--screenshot={raw_png}",
            f"file://{tmp_html.resolve()}",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if not raw_png.is_file():
            err = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(f"Chrome 截图失败: {err or 'no output file'}")
        crop_whitespace(raw_png, dest_png, pad=24)
    finally:
        if tmp_html.is_file():
            tmp_html.unlink()
        if raw_png.is_file():
            raw_png.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染并截取 Agent Rank 榜单预览图（200K 测速榜与单价榜）")
    ap.add_argument("--latest", default=str(DEFAULT_LATEST_200K), help="200K 测速源数据 (latest_200k.json)")
    ap.add_argument("--pricing", default=str(DEFAULT_LATEST_PRICING), help="单价源数据 (latest_pricing.json)")
    ap.add_argument("--out-speed", default=str(DEFAULT_OUT_SPEED), help="200K 测速榜输出路径")
    ap.add_argument("--out-pricing", default=str(DEFAULT_OUT_PRICING), help="单价榜输出路径")
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT_LEGACY), help="兼容输出路径（写入 200K 测速榜，供 README）")
    ap.add_argument("--limit-pricing", type=int, default=25, help="单价榜展示行数（默认前 25 行）")
    ap.add_argument("--width", type=int, default=1050, help="渲染视口宽度")
    ap.add_argument("--scale", type=int, default=2, help="设备像素比（2=Retina）")
    args = ap.parse_args(argv)

    chrome = find_chrome()
    latest_speed_path = Path(args.latest).expanduser().resolve()
    latest_pricing_path = Path(args.pricing).expanduser().resolve()
    out_speed = Path(args.out_speed).resolve()
    out_pricing = Path(args.out_pricing).resolve()
    out_legacy = Path(args.out).resolve() if args.out else None

    # 1. 200K 测速榜
    if latest_speed_path.is_file():
        speed_rows = json.loads(latest_speed_path.read_text(encoding="utf-8"))
        speed_html = render_speed_html(speed_rows)
        # 高度按行数自适应，避免多余计算
        est_height = max(800, len(speed_rows) * 56 + 200)
        chrome_render_to_png(chrome, speed_html, out_speed, width=args.width, height=est_height, scale=args.scale)
        print(f"Wrote speed board: {out_speed} ({out_speed.stat().st_size} bytes)")
        if out_legacy and out_legacy != out_speed:
            out_legacy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_speed, out_legacy)
            print(f"Updated legacy preview: {out_legacy}")
    else:
        print(f"Warning: {latest_speed_path} not found, skipped speed board", file=sys.stderr)

    # 2. 单价榜
    if latest_pricing_path.is_file():
        pricing_rows = json.loads(latest_pricing_path.read_text(encoding="utf-8"))
        if args.limit_pricing and args.limit_pricing > 0:
            pricing_rows = pricing_rows[: args.limit_pricing]
        pricing_html = render_pricing_html(pricing_rows)
        est_height = max(800, len(pricing_rows) * 56 + 200)
        chrome_render_to_png(chrome, pricing_html, out_pricing, width=args.width, height=est_height, scale=args.scale)
        print(f"Wrote pricing board: {out_pricing} ({out_pricing.stat().st_size} bytes)")
    else:
        print(f"Warning: {latest_pricing_path} not found, skipped pricing board", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"screenshot_board failed: {e}", file=sys.stderr)
        raise SystemExit(1)
