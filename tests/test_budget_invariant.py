import re
from pathlib import Path

from .test_create_campaign import call, ops

REPO = Path(__file__).parent.parent
SOURCES = [
    p for d in ("ads_app_mcp", "scripts") for p in (REPO / d).rglob("*.py")
]


def _mentioning(pattern: str) -> set[str]:
    return {
        p.relative_to(REPO).as_posix()
        for p in SOURCES
        if re.search(pattern, p.read_text(), re.I)
    }


def test_only_create_campaign_mentions_budgets():
    assert _mentioning(r"campaign_?budget") == {
        "ads_app_mcp/tools/create_campaign.py"
    }


def test_only_known_files_mention_target_cpa():
    # create_campaign sets the first target, set_target_cost (in
    # manage_campaign) moves it inside the guardrails, queries only reads it.
    assert _mentioning(r"target_?cpa") == {
        "ads_app_mcp/tools/create_campaign.py",
        "ads_app_mcp/tools/manage_campaign.py",
        "ads_app_mcp/queries.py",
    }


def test_create_builds_exactly_one_budget_create(fake_ads, guardrails_file):
    call(fake_ads)
    budgets = [
        o.campaign_budget_operation
        for o in ops(fake_ads)
        if o._pb.WhichOneof("operation") == "campaign_budget_operation"
    ]
    assert len(budgets) == 1
    assert budgets[0]._pb.WhichOneof("operation") == "create"
