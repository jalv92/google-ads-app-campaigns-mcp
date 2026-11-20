import asyncio
import json
from pathlib import Path

import ads_mcp.utils
import proto

from ads_app_mcp.server import build_server

GOLDEN = Path(__file__).with_name("golden_tools.json")
UPSTREAM = {
    "assets_create_asset",
    "assets_upload_video_asset",
    "customers_list_accessible_customers",
    "labels_manage_label_associations",
    "labels_manage_labels",
    "metadata_get_resource_metadata",
    "search_search",
}


def _names():
    return [t.name for t in asyncio.run(build_server().list_tools())]


def test_tool_list_matches_golden():
    assert sorted(_names()) == json.loads(GOLDEN.read_text())


def test_upstream_tools_are_exactly_the_known_seven():
    names = {n for n in _names() if not n.startswith("app_campaigns_")}
    assert names == UPSTREAM


def test_build_server_is_idempotent():
    assert build_server() is build_server()
    names = _names()
    assert len(names) == len(set(names))


def test_tools_that_commit_alone_defer_to_the_playbook():
    desc = {
        t.name: t.description for t in asyncio.run(build_server().list_tools())
    }
    for name in ("set_target_cost", "update_app_ad"):
        text = desc[f"app_campaigns_{name}"]
        assert "daily_review" in text and "playbook" in text, name
        assert "explicit user approval" not in text, name
        assert "Never suggest a target" not in text, name
    assert "Never suggest, round or invent a budget yourself." in (
        desc["app_campaigns_create_app_campaign"]
    )


def test_fake_ads_patches_client(fake_ads):
    assert ads_mcp.utils.get_googleads_client() is fake_ads.client
    assert isinstance(fake_ads.client.get_type("Campaign"), proto.Message)
    svc = fake_ads.client.get_service("CampaignService")
    assert fake_ads.client.get_service("CampaignService") is svc
    assert svc is fake_ads.services["CampaignService"]
