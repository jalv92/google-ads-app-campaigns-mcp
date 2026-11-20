import json

import pytest
from fastmcp.exceptions import ToolError

from ads_app_mcp.tools.manage_campaign import (
    CampaignStatus,
    set_campaign_status,
    set_campaign_targeting,
)

from .test_queries import row

CID = "1234567890"


def crit(fake, kind, constant, rn):
    r = fake.real.get_type("GoogleAdsRow")
    c = r.campaign_criterion
    c.resource_name = rn
    if kind == "location":
        c.type_ = fake.real.enums.CriterionTypeEnum.LOCATION
        c.location.geo_target_constant = f"geoTargetConstants/{constant}"
    else:
        c.type_ = fake.real.enums.CriterionTypeEnum.LANGUAGE
        c.language.language_constant = f"languageConstants/{constant}"
    return r


def feed(fake, *searches):
    fake.client.get_service("GoogleAdsService").search.side_effect = [
        list(s) for s in searches
    ]


def status_call(fake, status=CampaignStatus.ENABLED, **over):
    feed(fake, [row(fake)])
    args = dict(customer_id=CID, campaign_id=111, status=status)
    args.update(over)
    return set_campaign_status(**args)


def camp_svc(fake):
    return fake.client.get_service("CampaignService")


def test_status_proposal_shows_before_and_after(fake_ads):
    out = status_call(fake_ads)
    item = json.loads(out)["change_items"][0]
    assert item["field_readable"] == "Status"
    assert item["original_value_readable"] == "PAUSED"
    assert item["value_readable"] == "ENABLED"


def test_status_commit_updates_only_status(fake_ads):
    cs = json.loads(status_call(fake_ads))["checksum"]
    status_call(fake_ads, validate_only=False, checksum=cs)
    req = camp_svc(fake_ads).mutate_campaigns.call_args.kwargs["request"]
    assert not req.validate_only
    op = req.operations[0]
    assert list(op.update_mask.paths) == ["status"]
    assert op.update.resource_name == f"customers/{CID}/campaigns/111"
    assert op.update.status.name == "ENABLED"


def test_status_same_value_refuses(fake_ads):
    with pytest.raises(ToolError, match="status"):
        status_call(fake_ads, status=CampaignStatus.PAUSED)
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_status_refuses_non_app_campaign(fake_ads):
    feed(fake_ads, [row(fake_ads, channel="SEARCH", sub="UNSPECIFIED")])
    with pytest.raises(ToolError, match="App campaign"):
        set_campaign_status(CID, 111, CampaignStatus.ENABLED)
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_status_commit_blocked_when_unattended(fake_ads, monkeypatch):
    cs = json.loads(status_call(fake_ads))["checksum"]
    camp_svc(fake_ads).mutate_campaigns.reset_mock()
    monkeypatch.setenv("GOOGLE_ADS_APP_MCP_UNATTENDED", "1")
    with pytest.raises(ToolError, match="unattended"):
        status_call(fake_ads, validate_only=False, checksum=cs)
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_status_login_customer_id_is_bound_into_checksum(fake_ads):
    a = json.loads(status_call(fake_ads, login_customer_id="111-222-3333"))
    b = json.loads(status_call(fake_ads, login_customer_id="999"))
    assert a["checksum"] != b["checksum"]


def targeting(fake, locations=(2840,), **over):
    feed(
        fake,
        [row(fake)],
        [
            crit(fake, "location", i, f"customers/1/campaignCriteria/111~{i}")
            for i in locations
        ]
        + [
            crit(
                fake, "language", 1000, "customers/1/campaignCriteria/111~1000"
            )
        ],
    )
    args = dict(customer_id=CID, campaign_id=111)
    args.update(over)
    return set_campaign_targeting(**args)


def crit_svc(fake):
    return fake.client.get_service("CampaignCriterionService")


def items(out):
    return {i["field_readable"]: i for i in json.loads(out)["change_items"]}


def test_targeting_adds_and_removes(fake_ads):
    kw = dict(add_location_ids=[2124], remove_location_ids=[2840])
    cs = json.loads(targeting(fake_ads, **kw))["checksum"]
    targeting(fake_ads, validate_only=False, checksum=cs, **kw)
    req = crit_svc(fake_ads).mutate_campaign_criteria.call_args.kwargs[
        "request"
    ]
    assert not req.validate_only
    ops = list(req.operations)
    assert len(ops) == 2
    removes = [o for o in ops if o._pb.WhichOneof("operation") == "remove"]
    creates = [o for o in ops if o._pb.WhichOneof("operation") == "create"]
    assert [o.remove for o in removes] == [
        "customers/1/campaignCriteria/111~2840"
    ]
    assert (
        creates[0].create.location.geo_target_constant
        == "geoTargetConstants/2124"
    )
    assert creates[0].create.campaign == f"customers/{CID}/campaigns/111"


def test_targeting_shows_resulting_list(fake_ads):
    it = items(
        targeting(fake_ads, add_location_ids=[2124], remove_location_ids=[2840])
    )
    assert it["Locations added"]["value_readable"] == "2124"
    assert it["Locations after"]["value_readable"] == "2124"
    assert it["Locations after"]["original_value_readable"] == "2840"


def test_targeting_emptying_locations_says_all(fake_ads):
    it = items(targeting(fake_ads, remove_location_ids=[2840]))
    assert (
        it["Locations after"]["value_readable"] == "all (no location targeting)"
    )


def test_targeting_emptying_languages_says_all(fake_ads):
    it = items(targeting(fake_ads, remove_language_ids=[1000]))
    assert (
        it["Languages after"]["value_readable"] == "all (no language targeting)"
    )


def test_targeting_refuses_unknown_removal(fake_ads):
    with pytest.raises(ToolError, match="remove_location_ids"):
        targeting(fake_ads, remove_location_ids=[9999])
    crit_svc(fake_ads).mutate_campaign_criteria.assert_not_called()


def test_targeting_refuses_duplicate_add(fake_ads):
    with pytest.raises(ToolError, match="add_language_ids"):
        targeting(fake_ads, add_language_ids=[1000])
    crit_svc(fake_ads).mutate_campaign_criteria.assert_not_called()


def test_targeting_requires_a_change(fake_ads):
    with pytest.raises(ToolError, match="nothing to change"):
        set_campaign_targeting(CID, 111)


def test_targeting_commit_blocked_when_unattended(fake_ads, monkeypatch):
    kw = dict(add_location_ids=[2124])
    cs = json.loads(targeting(fake_ads, **kw))["checksum"]
    crit_svc(fake_ads).mutate_campaign_criteria.reset_mock()
    monkeypatch.setenv("GOOGLE_ADS_APP_MCP_UNATTENDED", "1")
    with pytest.raises(ToolError, match="unattended"):
        targeting(fake_ads, validate_only=False, checksum=cs, **kw)
    crit_svc(fake_ads).mutate_campaign_criteria.assert_not_called()


def test_targeting_refuses_non_app_campaign(fake_ads):
    feed(fake_ads, [row(fake_ads, channel="SEARCH", sub="UNSPECIFIED")])
    with pytest.raises(ToolError, match="App campaign"):
        set_campaign_targeting(CID, 111, add_location_ids=[2124])
    crit_svc(fake_ads).mutate_campaign_criteria.assert_not_called()


def test_targeting_login_customer_id_is_bound_into_checksum(fake_ads):
    a = json.loads(targeting(fake_ads, add_location_ids=[2124]))
    b = json.loads(
        targeting(fake_ads, add_location_ids=[2124], login_customer_id="999")
    )
    assert a["checksum"] != b["checksum"]


def test_status_commit_refused_when_campaign_changed(fake_ads):
    cs = json.loads(status_call(fake_ads))["checksum"]
    camp_svc(fake_ads).mutate_campaigns.reset_mock()
    # the campaign was enabled by someone else after the proposal
    r = row(fake_ads)
    r.campaign.status = fake_ads.real.enums.CampaignStatusEnum.ENABLED
    feed(fake_ads, [r])
    with pytest.raises(ToolError):
        set_campaign_status(
            CID, 111, CampaignStatus.PAUSED, validate_only=False, checksum=cs
        )
    camp_svc(fake_ads).mutate_campaigns.assert_not_called()


def test_targeting_commit_refused_when_targeting_changed(fake_ads):
    kw = dict(add_location_ids=[2124])
    cs = json.loads(targeting(fake_ads, **kw))["checksum"]
    crit_svc(fake_ads).mutate_campaign_criteria.reset_mock()
    with pytest.raises(ToolError):
        targeting(
            fake_ads,
            locations=(2840, 2826),
            validate_only=False,
            checksum=cs,
            **kw,
        )
    crit_svc(fake_ads).mutate_campaign_criteria.assert_not_called()
