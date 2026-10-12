"""t009: scripts/update_pricing.py 对齐 / 覆盖 / 产物契约测试。"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update_pricing.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing"
GITIGNORE_PATH = ROOT / ".gitignore"
AGENTS_PATH = ROOT / "AGENTS.md"
GUIDE_PATH = ROOT / "docs" / "guides" / "pricing_update.md"

# 保证可 import 脚本内函数
sys.path.insert(0, str(ROOT / "scripts"))
import update_pricing as up  # noqa: E402


REQUIRED_FIELDS = {
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
}


@pytest.fixture()
def fixture_models() -> list[dict]:
    return json.loads((FIXTURES / "models.json").read_text(encoding="utf-8"))


@pytest.fixture()
def fixture_adopted() -> list[dict]:
    with (FIXTURES / "adopted.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_align_exact_id_and_alias(fixture_models, fixture_adopted):
    """AC-003：id 与 pricing_aliases 精确命中进 latest；大小写不同进 unmatched。"""
    latest, unmatched = up.build_pricing(fixture_models, fixture_adopted)
    models = {r["model"] for r in latest}
    assert "deepseek-v4.1-flash" in models
    assert "MiniMax-M3" in models  # via alias minimax-m3
    assert "gpt-6-sol" in models

    unmatched_served = {u["served_model"] for u in unmatched}
    assert "UNKNOWN-MODEL" in unmatched_served
    assert "Deepseek-v4.1-flash" in unmatched_served  # case mismatch
    assert all(u["served_model"] not in {r.get("served_model") for r in latest} for u in unmatched)
    # latest 不含未对齐 served
    for u in unmatched:
        assert not any(
            r["model"] == "Deepseek-v4.1-flash" for r in latest
        )


def test_pricing_row_fields_and_sortable(fixture_models, fixture_adopted):
    """AC-002：字段齐全；model 属于模型表；可按 real_usd_per_mtok 排序。"""
    latest, _ = up.build_pricing(fixture_models, fixture_adopted)
    table_ids = {m["id"] for m in fixture_models}
    assert latest
    for row in latest:
        assert REQUIRED_FIELDS <= set(row.keys())
        assert row["model"] in table_ids
    reals = [r["real_usd_per_mtok"] for r in latest if r["real_usd_per_mtok"] is not None]
    assert reals == sorted(reals)


def test_opencode_deepseek_override(fixture_models, fixture_adopted):
    """AC-004：OpenCode Go × deepseek-v4.1-flash 额度 ×80/60，月费不变，真实单价变小，notes 含覆盖说明。"""
    latest, _ = up.build_pricing(fixture_models, fixture_adopted)
    row = next(r for r in latest if r["plan"] == "OpenCode Go" and r["model"] == "deepseek-v4.1-flash")
    scale = 80 / 60
    assert row["price_usd"] == 10.0
    assert row["monthly_tokens"] == pytest.approx(1552800000 * scale)
    assert row["monthly_yi"] == pytest.approx(15.528 * scale)
    expected_real = 10.0 / (row["monthly_tokens"] / 1e6)
    assert row["real_usd_per_mtok"] == pytest.approx(expected_real)
    assert row["real_usd_per_mtok"] < 0.0064399794
    assert row["notes"] and "本仓覆盖" in row["notes"] and "60" in row["notes"] and "80" in row["notes"]
    assert row["source"] == "opencode-go"


def test_opencode_deepseek_override_via_local_id():
    """当上游 served_model 为别名、但本仓 id 为 deepseek-v4.1-flash 时仍应用覆盖。"""
    models = [{"id": "deepseek-v4.1-flash", "pricing_aliases": ["ds-short-alias"]}]
    adopted = [
        {
            "plan_name": "OpenCode Go",
            "served_model": "ds-short-alias",
            "price_usd": "10.0",
            "monthly_tokens": "6000000",
            "monthly_yi": "6.0",
            "real_usd_per_mtok": "1.6666667",
            "decision_note": "",
        }
    ]
    latest, _ = up.build_pricing(models, adopted)
    assert len(latest) == 1
    assert latest[0]["model"] == "deepseek-v4.1-flash"
    assert latest[0]["monthly_tokens"] == 8000000
    assert "本仓覆盖" in latest[0]["notes"]


def test_opencode_other_deepseek_models_not_scaled():
    """OpenCode Go 其余 DeepSeek 行（V4 Flash / V4 Pro / Vision Exp）不再被本仓放大。"""
    models = [{"id": "deepseek-v4-flash"}]
    adopted = [
        {
            "plan_name": "OpenCode Go",
            "served_model": "deepseek-v4-flash",
            "price_usd": "10",
            "monthly_tokens": "1000000",
            "monthly_yi": "1.0",
            "real_usd_per_mtok": "10.0",
            "decision_note": "",
        }
    ]
    latest, _ = up.build_pricing(models, adopted)
    assert len(latest) == 1
    assert latest[0]["monthly_tokens"] == 1000000
    assert latest[0]["monthly_yi"] == pytest.approx(1.0)
    assert latest[0]["notes"] is None


def test_minimax_cn_plan_source_mapping():
    """MiniMax M Plan (CN) 映射到 minimax-official。"""
    models = [{"id": "minimax-m3.1-flash-preview"}]
    adopted = [
        {
            "plan_name": "MiniMax M Plan Go (CN)",
            "served_model": "minimax-m3.1-flash-preview",
            "price_usd": "7.23",
            "monthly_tokens": "1598000000",
            "monthly_yi": "15.98",
            "real_usd_per_mtok": "0.0045234823",
            "decision_note": "",
        }
    ]
    latest, _ = up.build_pricing(models, adopted)
    assert len(latest) == 1
    assert latest[0]["source"] == "minimax-official"


def test_command_code_goat_override(fixture_models, fixture_adopted):
    """AC-005：Command Code GOAT 月费 10.78，额度不变，真实单价按 10.78 重算。"""
    latest, _ = up.build_pricing(fixture_models, fixture_adopted)
    row = next(r for r in latest if r["plan"] == "Command Code GOAT" and r["model"] == "gpt-6-sol")
    assert row["price_usd"] == 10.78
    assert row["monthly_tokens"] == 92100000
    assert row["monthly_yi"] == pytest.approx(0.921)
    assert row["real_usd_per_mtok"] == pytest.approx(10.78 / (92100000 / 1e6))
    assert row["notes"] and "本仓覆盖" in row["notes"] and "10.78" in row["notes"]


def test_command_code_goat_no_promo_row_derived():
    """Command Code GOAT × deepseek-v4.1-flash：仅一条常态行，不再派生促销行。"""
    models = [{"id": "deepseek-v4.1-flash"}]
    adopted = [
        {
            "plan_name": "Command Code GOAT",
            "served_model": "deepseek-v4.1-flash",
            "price_usd": "10",
            "monthly_tokens": "6211200000",
            "monthly_yi": "62.112",
            "real_usd_per_mtok": "0.0016099948",
            "decision_note": "upstream goat note",
            "billing": "subscription",
            "source": "https://commandcode.ai/",
        }
    ]
    latest, unmatched = up.build_pricing(models, adopted)
    assert unmatched == []
    assert len(latest) == 1

    row = latest[0]
    assert row["plan"] == "Command Code GOAT"
    assert row["model"] == "deepseek-v4.1-flash"
    assert row["price_usd"] == 10.78
    assert row["monthly_tokens"] == 6211200000
    assert row["monthly_yi"] == pytest.approx(62.112)
    assert row["promo_until"] is None
    assert row["real_usd_per_mtok"] == pytest.approx(10.78 / 6211.2)
    assert row["notes"] and "本仓覆盖" in row["notes"] and "10.78" in row["notes"]


def test_cli_writes_outputs_when_complete(tmp_path):
    """全量对齐时写入 latest_pricing，unmatched 为空，退出码 0。"""
    models_path = tmp_path / "models.json"
    models_path.write_text((FIXTURES / "models_complete.json").read_text(encoding="utf-8"), encoding="utf-8")
    out_latest = tmp_path / "latest_pricing.json"
    out_unmatched = tmp_path / "unmatched_pricing.json"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--csv",
            str(FIXTURES / "adopted.csv"),
            "--models",
            str(models_path),
            "--out-latest",
            str(out_latest),
            "--out-unmatched",
            str(out_unmatched),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    latest = json.loads(out_latest.read_text(encoding="utf-8"))
    unmatched = json.loads(out_unmatched.read_text(encoding="utf-8"))
    assert isinstance(latest, list) and latest
    assert unmatched == []


def test_cli_fails_without_writing_latest_when_unmatched(tmp_path, fixture_models):
    """有未对齐时非零退出，写出 unmatched 诊断，不覆盖 latest_pricing。"""
    models_path = tmp_path / "models.json"
    models_path.write_text(json.dumps(fixture_models, ensure_ascii=False), encoding="utf-8")
    out_latest = tmp_path / "latest_pricing.json"
    sentinel = [{"sentinel": True}]
    out_latest.write_text(json.dumps(sentinel), encoding="utf-8")
    out_unmatched = tmp_path / "unmatched_pricing.json"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--csv",
            str(FIXTURES / "adopted.csv"),
            "--models",
            str(models_path),
            "--out-latest",
            str(out_latest),
            "--out-unmatched",
            str(out_unmatched),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "ERROR" in r.stdout or "ERROR" in r.stderr
    assert "未拿全" in r.stdout or "未拿全" in r.stderr
    assert json.loads(out_latest.read_text(encoding="utf-8")) == sentinel
    unmatched = json.loads(out_unmatched.read_text(encoding="utf-8"))
    assert isinstance(unmatched, list) and unmatched


def test_guide_and_agents_and_gitignore():
    """AC-006 / AC-007：指南存在；AGENTS/gitignore 声明两份定价产物与脚本。"""
    assert GUIDE_PATH.is_file()
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    assert "update_pricing.py" in guide
    assert "覆盖" in guide
    assert "未对齐" in guide

    agents = AGENTS_PATH.read_text(encoding="utf-8")
    assert "`scripts/update_pricing.py`" in agents or "update_pricing.py" in agents
    assert "latest_pricing.json" in agents
    assert "unmatched_pricing.json" in agents

    gi = GITIGNORE_PATH.read_text(encoding="utf-8")
    assert "!/data/latest_pricing.json" in gi
    assert "!/data/unmatched_pricing.json" in gi
