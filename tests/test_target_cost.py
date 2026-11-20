import json
from datetime import datetime, timedelta, timezone

import pytest
from fastmcp.exceptions import ToolError

from ads_app_mcp.tools.manage_campaign import set_target_cost

from .test_manage_campaign import camp_svc, feed
from .test_queries import row

CID = "1234567890"
ACTION = "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST"


def event(fake, days_ago, paths=("target_cpa.target_cpa_micros",), op="UPDATE"):
    r = fake.real.get_type("GoogleAdsRow")
    e = r.change_event
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    e.change_date_time = f"{when:%Y-%m-%d %H:%M:%S}"
    e.change_resource_type = (
        fake.real.enums.ChangeEventResourceTypeEnum.CAMPAIGN
    )
    e.resource_change_operation = getattr(
        fake.real.enums.ResourceChangeOperationEnum, op
    )
    e.changed_fields.paths.extend(paths)
    return r


def call(
    fake,
    target=1.20,
    current=1_000_000,
    history=(),
    goal=None,
    time_zone="UTC",
    **over,
):
    feed(
        fake,
        [row(fake, target=current, goal=goal, time_zone=time_zone)],
        list(history),
    )
    args = dict(customer_id=CID, campaign_id=111, target_cost=target)
    args.update(over)
    return set_target_cost(**args)


def old(fake):
    return [event(fake, 10)]


def test_proposal_within_limits(fake_ads, guardrails_file):
    out = call(fake_ads, history=old(fake_ads))
    item = {i["field_readable"]: i for i in json.loads(out)["change_items"]}
    assert item["Target cost"]["value_readable"] == "1.20 USD per install"
    assert (
        item["Target cost"]["original_value_readable"] == "1.00 USD per install"
    )
    assert item["Allowed range"]["value_readable"] == (
        "0.80–1.50 USD, max 20 % per change, 3 days between changes"
    )
    cs = json.loads(out)["checksum"]
    call(fake_ads, history=old(fake_ads), validate_only=False, checksum=cs)
    req = camp_svc(fake_ads).mutate_campaigns.call_args.kwargs["request"]
    assert not req.validate_only
    op = req.operations[0]
    assert list(op.update_mask.paths) == ["target_cpa.target_cpa_micros"]
    assert op.update.target_cpa.target_cpa_micros == 1_200_000
    assert op.update.resource_name == f"customers/{CID}/campaigns/111"


def refused(fake, match, **kw):
    with pytest.raises(ToolError, match=match) as exc:
        call(fake, **kw)
    camp_svc(fake).mutate_campaigns.assert_not_called()
    return str(exc.value)


def test_out_of_range_refuses(fake_ads, guardrails_file):
    msg = refused(
        fake_ads,
        "target_cost",
        target=1.60,
        current=1_400_000,
        history=old(fake_ads),
    )
    assert "USD" in msg
    assert msg.count("cannot be overridden") == 1


def test_step_too_large_refuses(fake_ads, guardrails_file):
    refused(fake_ads, "target_cost", target=1.30, history=old(fake_ads))


def test_cooldown_refuses(fake_ads, guardrails_file):
    refused(fake_ads, "days", history=[event(fake_ads, 1)])


def test_cooldown_counts_this_servers_own_commit(fake_ads, guardrails_file):
    # Google's change history lags by minutes, so it is still empty here.
    second = dict(target=1.44, current=1_200_000)
    cs2 = json.loads(call(fake_ads, **second))["checksum"]
    cs1 = json.loads(call(fake_ads))["checksum"]
    call(fake_ads, validate_only=False, checksum=cs1)
    calls = camp_svc(fake_ads).mutate_campaigns.call_count
    with pytest.raises(ToolError, match="last change was 0.0 days ago"):
        call(fake_ads, **second)
    with pytest.raises(ToolError, match="last change was 0.0 days ago"):
        call(fake_ads, **second, validate_only=False, checksum=cs2)
    assert camp_svc(fake_ads).mutate_campaigns.call_count == calls
    other = row(fake_ads, target=1_000_000)
    other.campaign.id = 222
    feed(fake_ads, [other], [])
    assert json.loads(set_target_cost(CID, 222, 1.20))["status"] == "PROPOSED"


def test_creation_counts_as_a_change(fake_ads, guardrails_file):
    ev = event(fake_ads, 1, paths=(), op="CREATE")
    refused(fake_ads, "days", history=[ev])


def test_unreadable_history_refuses(fake_ads, guardrails_file):
    fake_ads.client.get_service("GoogleAdsService").search.side_effect = [
        [row(fake_ads, target=1_000_000)],
        ToolError("api down"),
    ]
    with pytest.raises(ToolError, match="change history could not be read"):
        set_target_cost(CID, 111, 1.20)
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_unexpected_history_row_refuses(fake_ads, guardrails_file):
    bad = event(fake_ads, 10)
    bad.change_event.change_date_time = "garbage"
    with pytest.raises(ToolError, match="change history could not be read"):
        call(fake_ads, history=[bad])
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_no_guardrails_refuses(fake_ads, monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_APP_MCP_GUARDRAILS", raising=False)
    refused(fake_ads, "target_cost", history=old(fake_ads))


def test_campaign_without_target_refuses(fake_ads, guardrails_file):
    refused(
        fake_ads,
        "no target cost",
        current=0,
        goal="OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST",
    )


def test_non_app_campaign_refuses(fake_ads, guardrails_file):
    feed(fake_ads, [row(fake_ads, channel="SEARCH", sub="UNSPECIFIED")])
    with pytest.raises(ToolError, match="App campaign"):
        set_target_cost(CID, 111, 1.20)
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_action_goal_uses_action_range(fake_ads, guardrails_file):
    out = call(
        fake_ads,
        target=2.40,
        current=2_000_000,
        goal=ACTION,
        history=old(fake_ads),
    )
    item = json.loads(out)["change_items"][0]
    assert item["value_readable"] == "2.40 USD per in-app action"
    camp_svc(fake_ads).mutate_campaigns.reset_mock()
    refused(
        fake_ads,
        "target_cost",
        target=1.20,
        current=1_000_000,
        goal=ACTION,
        history=old(fake_ads),
    )


def test_commit_rechecks_current_state(fake_ads, guardrails_file):
    cs = json.loads(call(fake_ads, history=old(fake_ads)))["checksum"]
    camp_svc(fake_ads).mutate_campaigns.reset_mock()
    with pytest.raises(ToolError):
        call(
            fake_ads,
            current=900_000,
            history=old(fake_ads),
            validate_only=False,
            checksum=cs,
        )
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_commit_reruns_cooldown_check(fake_ads, guardrails_file):
    cs = json.loads(call(fake_ads, history=old(fake_ads)))["checksum"]
    camp_svc(fake_ads).mutate_campaigns.reset_mock()
    with pytest.raises(ToolError, match="days"):
        call(
            fake_ads,
            history=[event(fake_ads, 1)],
            validate_only=False,
            checksum=cs,
        )
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_checksum_binds_login_customer_id(fake_ads, guardrails_file):
    a = json.loads(
        call(fake_ads, history=old(fake_ads), login_customer_id="111")
    )
    b = json.loads(
        call(fake_ads, history=old(fake_ads), login_customer_id="222")
    )
    assert a["checksum"] != b["checksum"]


def test_commit_allowed_when_unattended(fake_ads, guardrails_file, monkeypatch):
    cs = json.loads(call(fake_ads, history=old(fake_ads)))["checksum"]
    monkeypatch.setenv("GOOGLE_ADS_APP_MCP_UNATTENDED", "1")
    out = call(
        fake_ads, history=old(fake_ads), validate_only=False, checksum=cs
    )
    assert "1.20 USD" in out
    req = camp_svc(fake_ads).mutate_campaigns.call_args.kwargs["request"]
    assert not req.validate_only


def test_checksum_binds_current_target(fake_ads, guardrails_file):
    cs = json.loads(call(fake_ads, history=old(fake_ads)))["checksum"]
    camp_svc(fake_ads).mutate_campaigns.reset_mock()
    # 1.00 -> 1.05 meanwhile: 1.20 is still a legal 14 % step.
    with pytest.raises(ToolError, match="checksum"):
        call(
            fake_ads,
            current=1_050_000,
            history=old(fake_ads),
            validate_only=False,
            checksum=cs,
        )
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


@pytest.mark.parametrize("zone", ["", "Not/AZone"])
def test_bad_account_time_zone_refuses(fake_ads, guardrails_file, zone):
    with pytest.raises(ToolError, match="change history could not be read"):
        call(fake_ads, history=old(fake_ads), time_zone=zone)
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()
