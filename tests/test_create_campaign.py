import json

import pytest
from fastmcp.exceptions import ToolError

from ads_app_mcp.tools.create_campaign import (
    AppStore,
    Goal,
    create_app_campaign,
)

CID = "1234567890"


def currency(fake, code="USD"):
    r = fake.real.get_type("GoogleAdsRow")
    r.customer.currency_code = code
    fake.client.get_service("GoogleAdsService").search.return_value = [r]


def call(fake, **over):
    currency(fake)
    args = dict(
        customer_id=CID,
        name="Camp",
        app_id="com.example.app",
        daily_budget=5.0,
        contains_eu_political_advertising=False,
        goal=Goal.INSTALLS_TARGET_COST,
        headlines=["one", "two"],
        descriptions=["desc"],
        target_cost=1.2,
        location_ids=[2840],
        language_ids=[1000],
    )
    args.update(over)
    return create_app_campaign(**args), args


def svc(fake):
    return fake.client.get_service("GoogleAdsService")


def ops(fake):
    return list(svc(fake).mutate.call_args.kwargs["request"].mutate_operations)


def kinds(fake):
    return [o._pb.WhichOneof("operation") for o in ops(fake)]


def test_proposal_builds_expected_operations(fake_ads, guardrails_file):
    out, _ = call(fake_ads)
    assert kinds(fake_ads) == [
        "campaign_budget_operation",
        "campaign_operation",
        "campaign_criterion_operation",
        "campaign_criterion_operation",
        "ad_group_operation",
        "ad_group_ad_operation",
    ]
    o = ops(fake_ads)
    assert o[0].campaign_budget_operation.create.amount_micros == 5_000_000
    assert o[0].campaign_budget_operation.create.explicitly_shared is False
    c = o[1].campaign_operation.create
    assert c.status.name == "PAUSED"
    assert c.advertising_channel_type.name == "MULTI_CHANNEL"
    assert c.advertising_channel_sub_type.name == "APP_CAMPAIGN"
    assert c.app_campaign_setting.app_id == "com.example.app"
    assert c.target_cpa.target_cpa_micros == 1_200_000
    assert c.campaign_budget == f"customers/{CID}/campaignBudgets/-1"
    assert (
        o[2].campaign_criterion_operation.create.location.geo_target_constant
        == "geoTargetConstants/2840"
    )
    assert svc(fake_ads).mutate.call_args.kwargs["request"].validate_only
    data = json.loads(out)
    assert data["status"] == "PROPOSED" and data["checksum"]


def test_change_items_show_money_with_currency(fake_ads, guardrails_file):
    out, _ = call(fake_ads)
    vals = {
        i["field_readable"]: i["value_readable"]
        for i in json.loads(out)["change_items"]
    }
    assert vals["Daily budget"] == "5.00 USD"
    assert vals["Target cost"] == "1.20 USD per install"
    assert vals["Status"] == "PAUSED"


def test_commit_requires_matching_checksum(fake_ads, guardrails_file):
    out, _ = call(fake_ads)
    with pytest.raises(ToolError):
        call(fake_ads, validate_only=False, checksum="bad")
    with pytest.raises(ToolError):
        call(fake_ads, validate_only=False, checksum=None)


def test_commit_sends_validate_only_false_and_reports_id(
    fake_ads, guardrails_file
):
    out, _ = call(fake_ads)
    cs = json.loads(out)["checksum"]
    resp = svc(fake_ads).mutate.return_value
    resp.mutate_operation_responses.__getitem__.return_value.campaign_result.resource_name = (
        f"customers/{CID}/campaigns/999"
    )
    msg, _ = call(fake_ads, validate_only=False, checksum=cs)
    assert not svc(fake_ads).mutate.call_args.kwargs["request"].validate_only
    assert "999" in msg


def test_no_target_goal_uses_maximize_conversions(fake_ads):
    out, _ = call(fake_ads, goal=Goal.INSTALLS_NO_TARGET, target_cost=None)
    c = ops(fake_ads)[1].campaign_operation.create
    assert (
        c._pb.WhichOneof("campaign_bidding_strategy") == "maximize_conversions"
    )
    vals = {
        i["field_readable"]: i["value_readable"]
        for i in json.loads(out)["change_items"]
    }
    assert vals["Target cost"] == "none (Google sets the cost)"


def test_target_goal_without_guardrails_refuses(fake_ads, monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_APP_MCP_GUARDRAILS", raising=False)
    with pytest.raises(ToolError):
        call(fake_ads)
    svc(fake_ads).mutate.assert_not_called()


def test_target_outside_range_refuses(fake_ads, guardrails_file):
    with pytest.raises(ToolError):
        call(fake_ads, target_cost=1.6)
    svc(fake_ads).mutate.assert_not_called()


def test_target_cost_rules(fake_ads, guardrails_file):
    with pytest.raises(ToolError, match="target_cost"):
        call(fake_ads, target_cost=None)
    with pytest.raises(ToolError, match="target_cost"):
        call(fake_ads, goal=Goal.INSTALLS_NO_TARGET, target_cost=1.0)


def test_in_app_goal_requires_conversion_actions(fake_ads, guardrails_file):
    with pytest.raises(ToolError, match="conversion_actions"):
        call(fake_ads, goal=Goal.IN_APP_ACTIONS_TARGET_COST, target_cost=3.0)
    call(
        fake_ads,
        goal=Goal.IN_APP_ACTIONS_TARGET_COST,
        target_cost=3.0,
        conversion_actions=["55"],
    )
    c = ops(fake_ads)[1].campaign_operation.create
    assert list(c.selective_optimization.conversion_actions) == [
        f"customers/{CID}/conversionActions/55"
    ]


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf")])
def test_bad_budget_refuses(fake_ads, budget):
    with pytest.raises(ToolError, match="daily_budget"):
        call(fake_ads, daily_budget=budget)


@pytest.mark.parametrize(
    "headlines", [["one"], ["a"] * 6, ["a", " "], ["same", "same"]]
)
def test_bad_headlines_refuse(fake_ads, headlines):
    with pytest.raises(ToolError, match="headlines"):
        call(fake_ads, headlines=headlines)


def test_unattended_blocks_commit_but_not_proposal(
    fake_ads, guardrails_file, monkeypatch
):
    monkeypatch.setenv("GOOGLE_ADS_APP_MCP_UNATTENDED", "1")
    out, _ = call(fake_ads)
    with pytest.raises(ToolError, match="unattended"):
        call(
            fake_ads,
            validate_only=False,
            checksum=json.loads(out)["checksum"],
        )


def test_login_customer_id_is_bound_into_checksum(fake_ads, guardrails_file):
    a, _ = call(fake_ads, login_customer_id="111-222-3333")
    b, _ = call(fake_ads, login_customer_id="999")
    ca, cb = json.loads(a)["checksum"], json.loads(b)["checksum"]
    assert ca != cb
    with pytest.raises(ToolError):
        call(
            fake_ads,
            login_customer_id="999",
            validate_only=False,
            checksum=ca,
        )


def test_change_items_show_conversion_actions_and_assets(
    fake_ads, guardrails_file
):
    out, _ = call(
        fake_ads,
        goal=Goal.IN_APP_ACTIONS_TARGET_COST,
        target_cost=3.0,
        conversion_actions=["55"],
        image_assets=["7"],
        youtube_video_assets=[f"customers/{CID}/assets/8"],
    )
    vals = {
        i["field_readable"]: i["value_readable"]
        for i in json.loads(out)["change_items"]
    }
    assert vals["Conversion actions"] == json.dumps(
        [f"customers/{CID}/conversionActions/55"]
    )
    assert vals["Image assets"] == json.dumps([f"customers/{CID}/assets/7"])
    assert vals["Video assets"] == json.dumps([f"customers/{CID}/assets/8"])
    out, _ = call(fake_ads)
    vals = {
        i["field_readable"]: i["value_readable"]
        for i in json.loads(out)["change_items"]
    }
    assert vals["Conversion actions"] == (
        "none (Google's default install tracking)"
    )
    assert vals["Image assets"] == "none"
    assert vals["Video assets"] == "none"


def test_no_criteria_reads_as_all(fake_ads, guardrails_file):
    out, _ = call(fake_ads, location_ids=None, language_ids=None)
    vals = {
        i["field_readable"]: i["value_readable"]
        for i in json.loads(out)["change_items"]
    }
    assert vals["Locations"] == "all (no location targeting)"
    assert vals["Languages"] == "all (no language targeting)"


def test_guardrail_refusal_names_parameter_in_currency_units(
    fake_ads, guardrails_file
):
    with pytest.raises(ToolError) as e:
        call(fake_ads, target_cost=1.6)
    msg = str(e.value)
    assert "'target_cost'" in msg and "1.60" in msg and "0.80" in msg
    assert "1.50" in msg and "micros" not in msg


def test_google_ads_exception_becomes_tool_error(fake_ads, guardrails_file):
    from google.ads.googleads.errors import GoogleAdsException

    exc = GoogleAdsException(
        None, None, fake_ads.real.get_type("GoogleAdsFailure"), "req-1"
    )
    currency(fake_ads)
    fake_ads.client.get_service("GoogleAdsService").search.side_effect = exc
    with pytest.raises(ToolError, match="req-1"):
        create_app_campaign(
            customer_id=CID,
            name="Camp",
            app_id="com.example.app",
            daily_budget=5.0,
            contains_eu_political_advertising=False,
            goal=Goal.INSTALLS_TARGET_COST,
            headlines=["one", "two"],
            descriptions=["desc"],
            target_cost=1.2,
        )
