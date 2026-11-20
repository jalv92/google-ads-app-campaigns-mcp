"""Replace the creatives of an App campaign's ad in place."""

import ads_mcp.utils as utils
from ads_mcp import propose_and_confirm as pac
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_app_mcp import guardrails, queries
from ads_app_mcp.tools import app_campaigns_mcp
from ads_app_mcp.tools.create_campaign import resource_name, validate_ad_texts


def _show(items: list[str]) -> str:
    return " | ".join(items) or "(none)"


@app_campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def update_app_ad(
    customer_id: str | int,
    campaign_id: str | int,
    headlines: list[str] | None = None,
    descriptions: list[str] | None = None,
    image_assets: list[str] | None = None,
    youtube_video_assets: list[str] | None = None,
    ad_group_id: str | int | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Replaces creatives of an App campaign's ad. Each list given REPLACES
    the whole current list; a list left out (None) is not touched. At least
    one list is required. The ad itself is updated in place.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call `update_app_ad` with `validate_only=True` (the default). After validation passes, the tool returns structured `change_items` and a `checksum` without changing anything.
    2. With a user in the conversation, show the `change_items` and wait for a yes. In an unattended run started by the `daily_review` prompt, follow the playbook (`resource://app-campaign-playbook`), which says when this tool may commit on its own.
    3. To commit, call `update_app_ad` with `validate_only=False`, the exact same parameters and the `checksum` returned by the previous call.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: The App campaign ID.
        headlines: New full list of 2-5 headlines.
        descriptions: New full list of 1-5 descriptions.
        image_assets: New full list of up to 20 image asset IDs or resource names.
        youtube_video_assets: New full list of up to 20 video asset IDs or resource names.
        ad_group_id: Ad group to update. Required only when the campaign has several ad groups.
        login_customer_id: Optional manager customer ID to use as the login-customer-id header.
        validate_only: If True (default), validates and returns `change_items` and a `checksum`. If False, requires `checksum`.
        checksum: Checksum from a prior `validate_only=True` call. Required when `validate_only=False`.
    """
    guardrails.ensure_commit_allowed("update_app_ad", validate_only)
    cid = utils.clean_customer_id(customer_id)
    if not cid:
        raise ToolError("Error: 'customer_id' must contain digits.")
    if all(
        x is None
        for x in (headlines, descriptions, image_assets, youtube_video_assets)
    ):
        raise ToolError(
            "Error: give at least one of 'headlines', 'descriptions', "
            "'image_assets' or 'youtube_video_assets'."
        )
    validate_ad_texts(
        headlines, descriptions, image_assets, youtube_video_assets
    )
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    info = queries.get_app_campaign(client, cid, campaign_id)
    ads = queries.get_app_ads(client, cid, info.campaign_id)
    if ad_group_id is not None:
        want = queries.clean_id(ad_group_id, "ad_group_id")
        ads = [a for a in ads if a.ad_group_id == want]
        if not ads:
            raise ToolError(
                f"Error: 'ad_group_id' {want} has no ad in campaign "
                f"{info.campaign_id}."
            )
    groups = list(dict.fromkeys(a.ad_group_id for a in ads))
    if len(groups) > 1:
        raise ToolError(
            f"Error: campaign {info.campaign_id} has several ad groups; "
            f"set 'ad_group_id' to one of: {', '.join(groups)}."
        )
    if len(ads) > 1:
        raise ToolError(
            f"Error: ad group {groups[0]} has several ads; this tool only "
            "updates an ad group with exactly one ad."
        )
    if not ads:
        raise ToolError(f"Error: campaign {info.campaign_id} has no ad.")
    ad = ads[0]

    images = (
        None
        if image_assets is None
        else [resource_name(i, cid, "assets") for i in image_assets]
    )
    videos = (
        None
        if youtube_video_assets is None
        else [resource_name(i, cid, "assets") for i in youtube_video_assets]
    )
    service = utils.get_googleads_service_from_client(client, "AdService")
    op = client.get_type("AdOperation")
    op.update.resource_name = ad.ad_resource_name
    app_ad = op.update.app_ad
    # (new value, current value, label, request field, asset type, mask path)
    parts = (
        (
            headlines,
            ad.headlines,
            "Headlines",
            app_ad.headlines,
            "AdTextAsset",
            "headlines",
        ),
        (
            descriptions,
            ad.descriptions,
            "Descriptions",
            app_ad.descriptions,
            "AdTextAsset",
            "descriptions",
        ),
        (
            images,
            ad.image_assets,
            "Image assets",
            app_ad.images,
            "AdImageAsset",
            "images",
        ),
        (
            videos,
            ad.youtube_video_assets,
            "Video assets",
            app_ad.youtube_videos,
            "AdVideoAsset",
            "youtube_videos",
        ),
    )
    fields: dict[str, str] = {}
    before: dict[str, str] = {}
    for new, old, label, field, type_name, path in parts:
        if new is None:
            continue
        for value in new:
            a = client.get_type(type_name)
            if type_name == "AdTextAsset":
                a.text = value
            else:
                a.asset = value
            field.append(a)
        op.update_mask.paths.append(f"app_ad.{path}")
        fields[label] = _show(new)
        before[label] = _show(old)

    request = client.get_type("MutateAdsRequest")
    request.customer_id = cid
    request.operations.append(op)

    change_items = pac.build_change_items(
        "UPDATE",
        "App ad",
        f"{info.name} ({info.campaign_id}), ad group {ad.ad_group_id}",
        fields,
        before,
    )
    operation_payload = {
        "tool": "update_app_ad",
        "customer_id": cid,
        "login_customer_id": (
            utils.clean_customer_id(login_customer_id)
            if login_customer_id is not None
            else None
        ),
        "campaign_id": info.campaign_id,
        "headlines": headlines,
        "descriptions": descriptions,
        "image_assets": image_assets,
        "youtube_video_assets": youtube_video_assets,
        "ad_group_id": ad.ad_group_id,
        "current": before,
    }

    def _validate() -> None:
        request.validate_only = True
        service.mutate_ads(request=request)

    def _commit() -> str:
        request.validate_only = False
        service.mutate_ads(request=request)
        return (
            f"Updated the ad of campaign {info.campaign_id} "
            f"(ad group {ad.ad_group_id}): {', '.join(fields)}."
        )

    return pac.propose_and_confirm(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to update App ad",
    )
