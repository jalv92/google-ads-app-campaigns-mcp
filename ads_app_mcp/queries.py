"""Small read layer shared by the app campaign tools."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import ads_mcp.utils as utils
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException

from ads_app_mcp.guardrails import MAX_COOLDOWN_DAYS, GoalKind

_GOAL_KINDS = {
    "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST": GoalKind.INSTALL,
    "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST": GoalKind.ACTION,
}


@dataclass(frozen=True)
class AppAd:
    ad_group_id: str
    ad_resource_name: str
    headlines: list[str]
    descriptions: list[str]
    image_assets: list[str]
    youtube_video_assets: list[str]


@dataclass(frozen=True)
class CampaignInfo:
    campaign_id: str
    name: str
    status: str
    goal_type: str
    target_cost_micros: int | None
    currency_code: str
    time_zone: str

    @property
    def goal_kind(self) -> GoalKind | None:
        return _GOAL_KINDS.get(self.goal_type)


def clean_id(value, name: str = "campaign_id") -> str:
    """The digits of an ID, or of the ID at the end of a resource name."""
    out = re.sub(r"\D", "", utils.extract_id(str(value)))
    if not out:
        raise ToolError(f"Error: '{name}' must contain digits.")
    return out


def touches_target(paths) -> bool:
    """Whether a change_event's changed_fields name the target cost."""
    return any(p == "target_cpa" or p.startswith("target_cpa.") for p in paths)


def _search(client, customer_id: str, query: str):
    service = utils.get_googleads_service_from_client(
        client, "GoogleAdsService"
    )
    try:
        return list(service.search(customer_id=customer_id, query=query))
    except GoogleAdsException as e:
        raise ToolError(utils.format_google_ads_error(e)) from e


def get_currency_code(client, customer_id: str) -> str:
    rows = _search(
        client, customer_id, "SELECT customer.currency_code FROM customer"
    )
    if not rows:
        raise ToolError(
            f"Error: could not read the currency of customer {customer_id}."
        )
    return rows[0].customer.currency_code


def get_app_campaign(
    client, customer_id: str, campaign_id: str | int
) -> CampaignInfo:
    cid = clean_id(campaign_id)
    rows = _search(
        client,
        customer_id,
        "SELECT campaign.id, campaign.name, campaign.status, "
        "campaign.advertising_channel_type, "
        "campaign.advertising_channel_sub_type, "
        "campaign.app_campaign_setting.bidding_strategy_goal_type, "
        "campaign.target_cpa.target_cpa_micros, customer.currency_code, "
        "customer.time_zone "
        f"FROM campaign WHERE campaign.id = {cid}",
    )
    if not rows:
        raise ToolError(
            f"Error: campaign {cid} was not found in customer {customer_id}."
        )
    c = rows[0].campaign
    if (
        c.advertising_channel_type.name != "MULTI_CHANNEL"
        or c.advertising_channel_sub_type.name != "APP_CAMPAIGN"
    ):
        raise ToolError(
            f"Error: campaign {cid} is not an App campaign; this tool "
            "only works on App campaigns."
        )
    return CampaignInfo(
        campaign_id=str(c.id),
        name=c.name,
        status=c.status.name,
        goal_type=c.app_campaign_setting.bidding_strategy_goal_type.name,
        target_cost_micros=c.target_cpa.target_cpa_micros or None,
        currency_code=rows[0].customer.currency_code,
        time_zone=rows[0].customer.time_zone,
    )


_CHANGE_EVENT_LIMIT = 10000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def days_since_target_change(
    client,
    customer_id: str,
    campaign_id: str | int,
    time_zone: str,
    now: datetime | None = None,
) -> float | None:
    """Days since the newest event that created the campaign or changed its
    target cost; None when the last MAX_COOLDOWN_DAYS + 1 days have none.

    Raises on any API error, unexpected row, or an unknown or empty
    `time_zone`, so the caller can refuse.

    change_event timestamps and the date bounds of the query are in the
    account's time zone, so "now" is taken in that zone (`time_zone` is the
    IANA name from customer.time_zone). `now` overrides it for tests and is
    read as account-local time. As extra margin an event counts as happening
    at the END of its calendar day, and a negative age is clamped to 0.
    """
    cid = clean_id(campaign_id)
    if now is None:
        now = _utcnow().astimezone(ZoneInfo(time_zone)).replace(tzinfo=None)
    start = now - timedelta(days=MAX_COOLDOWN_DAYS + 1)
    rows = _search(
        client,
        customer_id,
        "SELECT change_event.change_date_time, "
        "change_event.change_resource_type, "
        "change_event.resource_change_operation, "
        "change_event.changed_fields FROM change_event "
        f"WHERE change_event.campaign = 'customers/{customer_id}/campaigns/{cid}' "
        "AND change_event.change_resource_type = 'CAMPAIGN' "
        f"AND change_event.change_date_time >= '{start:%Y-%m-%d} 00:00:00' "
        f"AND change_event.change_date_time <= '{now:%Y-%m-%d} 23:59:59' "
        f"ORDER BY change_event.change_date_time DESC LIMIT {_CHANGE_EVENT_LIMIT}",
    )
    newest = None
    for r in rows:
        e = r.change_event
        paths = list(e.changed_fields.paths)
        # Skip only rows that clearly are not a target change: a CREATE
        # counts, and so does a row whose changed_fields are empty.
        if (
            e.resource_change_operation.name != "CREATE"
            and paths
            and not touches_target(paths)
        ):
            continue
        day = datetime.strptime(e.change_date_time[:10], "%Y-%m-%d")
        end_of_day = day + timedelta(days=1)
        if newest is None or end_of_day > newest:
            newest = end_of_day
    if newest is None:
        if len(rows) >= _CHANGE_EVENT_LIMIT:
            raise ToolError("Error: change history was truncated.")
        return None
    return max(0.0, (now - newest).total_seconds() / 86400)


def get_app_ads(
    client, customer_id: str, campaign_id: str | int
) -> list[AppAd]:
    """The non-removed App ad of each ad group of the campaign."""
    cid = clean_id(campaign_id)
    rows = _search(
        client,
        customer_id,
        "SELECT ad_group.id, ad_group_ad.ad.resource_name, "
        "ad_group_ad.ad.app_ad.headlines, ad_group_ad.ad.app_ad.descriptions, "
        "ad_group_ad.ad.app_ad.images, ad_group_ad.ad.app_ad.youtube_videos "
        f"FROM ad_group_ad WHERE campaign.id = {cid} "
        "AND ad_group_ad.status != 'REMOVED' "
        "AND ad_group.status != 'REMOVED'",
    )
    ads = []
    for r in rows:
        app_ad = r.ad_group_ad.ad.app_ad
        ads.append(
            AppAd(
                ad_group_id=str(r.ad_group.id),
                ad_resource_name=r.ad_group_ad.ad.resource_name,
                headlines=[a.text for a in app_ad.headlines],
                descriptions=[a.text for a in app_ad.descriptions],
                image_assets=[a.asset for a in app_ad.images],
                youtube_video_assets=[a.asset for a in app_ad.youtube_videos],
            )
        )
    return ads


def get_targeting(
    client, customer_id: str, campaign_id: str | int
) -> dict[str, dict[str, str]]:
    """Positive location and language criteria: {kind: {id: resource name}}."""
    cid = clean_id(campaign_id)
    rows = _search(
        client,
        customer_id,
        "SELECT campaign_criterion.resource_name, campaign_criterion.type, "
        "campaign_criterion.location.geo_target_constant, "
        "campaign_criterion.language.language_constant "
        f"FROM campaign_criterion WHERE campaign.id = {cid} "
        "AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE') "
        "AND campaign_criterion.negative = FALSE "
        "AND campaign_criterion.status != 'REMOVED'",
    )
    out: dict[str, dict[str, str]] = {"location": {}, "language": {}}
    for r in rows:
        c = r.campaign_criterion
        kind = c.type_.name.lower()
        if kind == "location":
            constant = c.location.geo_target_constant
        elif kind == "language":
            constant = c.language.language_constant
        else:
            continue
        out[kind][constant.rsplit("/", 1)[-1]] = c.resource_name
    return out
