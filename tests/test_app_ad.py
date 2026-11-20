import json

import pytest
from fastmcp.exceptions import ToolError

from ads_app_mcp.tools.app_ad import update_app_ad

from .test_queries import row

CID = "1234567890"


def ad_row(fake, group=7, heads=("old one", "old two"), descs=("old desc",)):
    r = fake.real.get_type("GoogleAdsRow")
    r.ad_group.id = group
    r.ad_group_ad.ad.resource_name = f"customers/{CID}/ads/{group}"
    for texts, field in (
        (heads, r.ad_group_ad.ad.app_ad.headlines),
        (descs, r.ad_group_ad.ad.app_ad.descriptions),
    ):
        for t in texts:
            a = fake.real.get_type("AdTextAsset")
            a.text = t
            field.append(a)
    return r


def call(fake, ads=None, **over):
    fake.client.get_service("GoogleAdsService").search.side_effect = [
        [row(fake)],
        ads if ads is not None else [ad_row(fake)],
    ]
    args = dict(customer_id=CID, campaign_id=111)
    args.update(over)
    return update_app_ad(**args)


def ad_svc(fake):
    return fake.client.get_service("AdService")


def test_replaces_only_given_lists(fake_ads):
    kw = dict(headlines=["new one", "new two", "new three"])
    cs = json.loads(call(fake_ads, **kw))["checksum"]
    call(fake_ads, validate_only=False, checksum=cs, **kw)
    req = ad_svc(fake_ads).mutate_ads.call_args.kwargs["request"]
    assert not req.validate_only
    op = req.operations[0]
    assert list(op.update_mask.paths) == ["app_ad.headlines"]
    assert op.update.resource_name == f"customers/{CID}/ads/7"
    assert [h.text for h in op.update.app_ad.headlines] == kw["headlines"]
    assert len(op.update.app_ad.descriptions) == 0


def test_change_items_show_old_and_new_texts(fake_ads):
    out = call(fake_ads, headlines=["new one", "new two"])
    (item,) = json.loads(out)["change_items"]
    assert item["field_readable"] == "Headlines"
    assert item["value_readable"] == "new one | new two"
    assert item["original_value_readable"] == "old one | old two"


def test_assets_use_resource_names_and_clearing_is_allowed(fake_ads):
    cs = json.loads(
        call(fake_ads, image_assets=["55"], youtube_video_assets=[])
    )["checksum"]
    call(
        fake_ads,
        image_assets=["55"],
        youtube_video_assets=[],
        validate_only=False,
        checksum=cs,
    )
    op = ad_svc(fake_ads).mutate_ads.call_args.kwargs["request"].operations[0]
    assert list(op.update_mask.paths) == [
        "app_ad.images",
        "app_ad.youtube_videos",
    ]
    assert [i.asset for i in op.update.app_ad.images] == [
        f"customers/{CID}/assets/55"
    ]


def test_requires_at_least_one_list(fake_ads):
    with pytest.raises(ToolError, match="at least one"):
        call(fake_ads)
    ad_svc(fake_ads).mutate_ads.assert_not_called()


def test_validates_counts(fake_ads):
    with pytest.raises(ToolError, match="headlines"):
        call(fake_ads, headlines=["only one"])
    ad_svc(fake_ads).mutate_ads.assert_not_called()


def test_multiple_ad_groups_need_ad_group_id(fake_ads):
    ads = lambda: [ad_row(fake_ads, 7), ad_row(fake_ads, 8)]
    with pytest.raises(ToolError, match="ad_group_id.*7, 8"):
        call(fake_ads, ads(), descriptions=["d"])
    out = call(fake_ads, ads(), descriptions=["d"], ad_group_id=8)
    assert (
        "ad group 8"
        in json.loads(out)["change_items"][0]["primary_key_readable"]
    )
    with pytest.raises(ToolError, match="ad_group_id"):
        call(fake_ads, ads(), descriptions=["d"], ad_group_id=99)


def test_ad_group_id_may_be_a_resource_name(fake_ads):
    ads = [ad_row(fake_ads, 7), ad_row(fake_ads, 8)]
    out = call(
        fake_ads,
        ads,
        descriptions=["d"],
        ad_group_id=f"customers/{CID}/adGroups/8",
    )
    key = json.loads(out)["change_items"][0]["primary_key_readable"]
    assert key.endswith("ad group 8")


def test_ad_group_with_several_ads_refuses(fake_ads):
    ads = lambda: [ad_row(fake_ads, 7), ad_row(fake_ads, 7)]
    with pytest.raises(ToolError, match="several ads"):
        call(fake_ads, ads(), descriptions=["d"], ad_group_id=7)
    with pytest.raises(ToolError, match="several ads"):
        call(fake_ads, ads(), descriptions=["d"])
    ad_svc(fake_ads).mutate_ads.assert_not_called()


def test_commit_allowed_when_unattended(fake_ads, monkeypatch):
    kw = dict(descriptions=["new desc"])
    cs = json.loads(call(fake_ads, **kw))["checksum"]
    monkeypatch.setenv("GOOGLE_ADS_APP_MCP_UNATTENDED", "1")
    call(fake_ads, validate_only=False, checksum=cs, **kw)
    assert (
        not ad_svc(fake_ads)
        .mutate_ads.call_args.kwargs["request"]
        .validate_only
    )


def test_refuses_non_app_campaign(fake_ads):
    fake_ads.client.get_service("GoogleAdsService").search.side_effect = [
        [row(fake_ads, channel="SEARCH", sub="UNSPECIFIED")]
    ]
    with pytest.raises(ToolError, match="App campaign"):
        update_app_ad(CID, 111, headlines=["a", "b"])
    ad_svc(fake_ads).mutate_ads.assert_not_called()


def test_no_ad_refuses(fake_ads):
    with pytest.raises(ToolError, match="no ad"):
        call(fake_ads, [], descriptions=["d"])


def test_refused_commit_leaves_mutate_uncalled(fake_ads):
    with pytest.raises(ToolError):
        call(fake_ads, descriptions=["d"], validate_only=False, checksum="bad")
    for c in ad_svc(fake_ads).mutate_ads.call_args_list:
        assert c.kwargs["request"].validate_only


def test_commit_refused_when_ad_changed_after_proposal(fake_ads):
    kw = dict(headlines=["new one", "new two"])
    cs = json.loads(call(fake_ads, **kw))["checksum"]
    ad_svc(fake_ads).mutate_ads.reset_mock()
    with pytest.raises(ToolError):
        call(
            fake_ads,
            [ad_row(fake_ads, heads=("edited elsewhere", "x"))],
            validate_only=False,
            checksum=cs,
            **kw,
        )
    ad_svc(fake_ads).mutate_ads.assert_not_called()
