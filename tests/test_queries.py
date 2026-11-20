from datetime import datetime, timezone

import pytest
from fastmcp.exceptions import ToolError

from ads_app_mcp import queries
from ads_app_mcp.guardrails import GoalKind


def row(
    fake,
    channel="MULTI_CHANNEL",
    sub="APP_CAMPAIGN",
    goal=None,
    target=0,
    time_zone="UTC",
):
    r = fake.real.get_type("GoogleAdsRow")
    e = fake.real.enums
    r.campaign.id = 111
    r.campaign.name = "c"
    r.campaign.status = e.CampaignStatusEnum.PAUSED
    r.campaign.advertising_channel_type = getattr(
        e.AdvertisingChannelTypeEnum, channel
    )
    r.campaign.advertising_channel_sub_type = getattr(
        e.AdvertisingChannelSubTypeEnum, sub
    )
    goal = goal or "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST"
    r.campaign.app_campaign_setting.bidding_strategy_goal_type = getattr(
        e.AppCampaignBiddingStrategyGoalTypeEnum, goal
    )
    r.campaign.target_cpa.target_cpa_micros = target
    r.customer.currency_code = "USD"
    r.customer.time_zone = time_zone
    return r


def rows(fake, *rs):
    fake.services.setdefault(
        "GoogleAdsService", fake.client.get_service("GoogleAdsService")
    )
    fake.services["GoogleAdsService"].search.return_value = list(rs)


def test_get_app_campaign_maps_row(fake_ads):
    rows(fake_ads, row(fake_ads, target=1_200_000))
    info = queries.get_app_campaign(fake_ads.client, "1234567890", 111)
    assert info.target_cost_micros == 1_200_000
    assert info.goal_kind is GoalKind.INSTALL
    assert info.currency_code == "USD"
    assert info.campaign_id == "111"


def test_get_app_campaign_refuses_other_types(fake_ads):
    rows(fake_ads, row(fake_ads, channel="SEARCH", sub="UNSPECIFIED"))
    with pytest.raises(ToolError, match="App campaign"):
        queries.get_app_campaign(fake_ads.client, "1234567890", 111)


RN = "customers/1234567890/campaigns/111"


@pytest.mark.parametrize(
    "fn,args,expected",
    [
        (queries.get_app_campaign, (), "WHERE campaign.id = 111"),
        (
            queries.days_since_target_change,
            ("UTC",),
            "campaign = 'customers/1234567890/campaigns/111'",
        ),
        (queries.get_app_ads, (), "WHERE campaign.id = 111 AND"),
        (queries.get_targeting, (), "WHERE campaign.id = 111 AND"),
    ],
)
def test_campaign_id_may_be_a_resource_name(fake_ads, fn, args, expected):
    if fn is queries.get_app_campaign:
        rows(fake_ads, row(fake_ads))
    else:
        rows(fake_ads)
    fn(fake_ads.client, "1234567890", RN, *args)
    assert expected in _query(fake_ads)


def test_get_app_campaign_refuses_missing(fake_ads):
    rows(fake_ads)
    with pytest.raises(ToolError):
        queries.get_app_campaign(fake_ads.client, "1234567890", 111)


def test_goal_kind_none_without_target(fake_ads):
    rows(
        fake_ads,
        row(fake_ads, goal="OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST"),
    )
    info = queries.get_app_campaign(fake_ads.client, "1234567890", 111)
    assert info.goal_kind is None
    assert info.target_cost_micros is None


def test_get_app_campaign_refuses_other_multi_channel_subtypes(fake_ads):
    rows(fake_ads, row(fake_ads, sub="APP_CAMPAIGN_FOR_ENGAGEMENT"))
    with pytest.raises(ToolError, match="App campaign"):
        queries.get_app_campaign(fake_ads.client, "1234567890", 111)


def test_get_app_ads_maps_rows(fake_ads):
    r = fake_ads.real.get_type("GoogleAdsRow")
    r.ad_group.id = 7
    r.ad_group_ad.ad.resource_name = "customers/1/ads/9"
    a = fake_ads.real.get_type("AdTextAsset")
    a.text = "head"
    r.ad_group_ad.ad.app_ad.headlines.append(a)
    v = fake_ads.real.get_type("AdVideoAsset")
    v.asset = "customers/1/assets/5"
    r.ad_group_ad.ad.app_ad.youtube_videos.append(v)
    rows(fake_ads, r)
    (ad,) = queries.get_app_ads(fake_ads.client, "1234567890", 111)
    assert ad == queries.AppAd(
        "7", "customers/1/ads/9", ["head"], [], [], ["customers/1/assets/5"]
    )


def test_get_targeting_maps_positive_criteria(fake_ads):
    r = fake_ads.real.get_type("GoogleAdsRow")
    r.campaign_criterion.resource_name = "customers/1/campaignCriteria/111~2840"
    r.campaign_criterion.type_ = fake_ads.real.enums.CriterionTypeEnum.LOCATION
    r.campaign_criterion.location.geo_target_constant = (
        "geoTargetConstants/2840"
    )
    rows(fake_ads, r)
    assert queries.get_targeting(fake_ads.client, "1234567890", 111) == {
        "location": {"2840": "customers/1/campaignCriteria/111~2840"},
        "language": {},
    }


def _query(fake):
    svc = fake.client.get_service("GoogleAdsService")
    return svc.search.call_args.kwargs["query"]


def test_get_app_ads_skips_removed_ads_and_removed_ad_groups(fake_ads):
    rows(fake_ads)
    queries.get_app_ads(fake_ads.client, "1234567890", 111)
    q = _query(fake_ads)
    assert "ad_group_ad.status != 'REMOVED'" in q
    assert "ad_group.status != 'REMOVED'" in q


def test_get_targeting_skips_removed_criteria(fake_ads):
    rows(fake_ads)
    queries.get_targeting(fake_ads.client, "1234567890", 111)
    assert "campaign_criterion.status != 'REMOVED'" in _query(fake_ads)


NOW = datetime(2026, 11, 20, 12, 0)


def change(fake, when, paths=(), op="UPDATE"):
    r = fake.real.get_type("GoogleAdsRow")
    e = r.change_event
    e.change_date_time = when
    e.change_resource_type = (
        fake.real.enums.ChangeEventResourceTypeEnum.CAMPAIGN
    )
    e.resource_change_operation = getattr(
        fake.real.enums.ResourceChangeOperationEnum, op
    )
    e.changed_fields.paths.extend(paths)
    return r


def test_days_since_target_change_picks_newest_target_event(fake_ads):
    rows(
        fake_ads,
        change(fake_ads, "2026-11-19 12:00:00", ["status"]),
        change(
            fake_ads, "2026-11-16 12:00:00", ["target_cpa.target_cpa_micros"]
        ),
        change(fake_ads, "2026-11-10 08:00:00.123456", ["target_cpa"]),
    )
    # The event counts as the END of its day (2026-11-17 00:00): 3.5 days.
    got = queries.days_since_target_change(
        fake_ads.client, "1234567890", 111, "UTC", now=NOW
    )
    assert got == 3.5
    q = _query(fake_ads)
    assert "change_event.campaign = 'customers/1234567890/campaigns/111'" in q
    assert "change_event.change_date_time >= '2026-10-22 00:00:00'" in q
    assert "LIMIT 10000" in q


def test_days_since_target_change_counts_campaign_creation(fake_ads):
    rows(fake_ads, change(fake_ads, "2026-11-18 09:00:00", op="CREATE"))
    got = queries.days_since_target_change(
        fake_ads.client, "1234567890", 111, "UTC", now=NOW
    )
    assert got == 1.5


def test_days_since_target_change_counts_update_without_fields(fake_ads):
    # An UPDATE whose changed_fields is empty is unclear, so it counts.
    rows(fake_ads, change(fake_ads, "2026-11-18 09:00:00"))
    got = queries.days_since_target_change(
        fake_ads.client, "1234567890", 111, "UTC", now=NOW
    )
    assert got == 1.5


def test_days_since_target_change_none_when_no_event(fake_ads):
    rows(fake_ads, change(fake_ads, "2026-11-19 12:00:00", ["status"]))
    assert (
        queries.days_since_target_change(
            fake_ads.client, "1234567890", 111, "UTC", now=NOW
        )
        is None
    )


def test_days_since_target_change_same_day_is_zero(fake_ads):
    rows(fake_ads, change(fake_ads, "2026-11-20 01:00:00", ["target_cpa"]))
    assert (
        queries.days_since_target_change(
            fake_ads.client, "1234567890", 111, "UTC", now=NOW
        )
        == 0.0
    )


def test_days_since_target_change_propagates_api_error(fake_ads):
    fake_ads.client.get_service("GoogleAdsService").search.side_effect = (
        ToolError("boom")
    )
    with pytest.raises(ToolError):
        queries.days_since_target_change(
            fake_ads.client, "1234567890", 111, "UTC"
        )


def _fix_instant(monkeypatch, *args):
    monkeypatch.setattr(
        queries, "_utcnow", lambda: datetime(*args, tzinfo=timezone.utc)
    )


def test_days_since_target_change_bounds_use_account_date(
    fake_ads, monkeypatch
):
    # 20:00 UTC on the 20th is already the 21st in Tokyo.
    _fix_instant(monkeypatch, 2026, 11, 20, 20, 0)
    rows(fake_ads)
    queries.days_since_target_change(
        fake_ads.client, "1234567890", 111, "Asia/Tokyo"
    )
    q = _query(fake_ads)
    assert "change_date_time <= '2026-11-21 23:59:59'" in q
    assert "change_date_time >= '2026-10-23 00:00:00'" in q


def test_days_since_target_change_age_uses_account_clock(fake_ads, monkeypatch):
    # 03:00 UTC on the 21st is 22:00 on the 20th in New York.
    _fix_instant(monkeypatch, 2026, 11, 21, 3, 0)
    rows(fake_ads, change(fake_ads, "2026-11-19 23:30:00", ["target_cpa"]))
    got = queries.days_since_target_change(
        fake_ads.client, "1234567890", 111, "America/New_York"
    )
    assert got == pytest.approx(22 / 24)


@pytest.mark.parametrize("zone", ["", "Not/AZone"])
def test_days_since_target_change_bad_zone_raises(fake_ads, zone):
    rows(fake_ads)
    with pytest.raises(Exception):
        queries.days_since_target_change(
            fake_ads.client, "1234567890", 111, zone
        )
