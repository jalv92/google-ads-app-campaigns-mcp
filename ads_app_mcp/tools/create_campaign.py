"""Create a whole App campaign in one atomic request."""

import math
from enum import Enum

import ads_mcp.utils as utils
from ads_mcp import propose_and_confirm as pac
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_app_mcp import guardrails, queries
from ads_app_mcp.guardrails import GoalKind, GuardrailError
from ads_app_mcp.tools import app_campaigns_mcp


class Goal(str, Enum):
    INSTALLS_TARGET_COST = "INSTALLS_TARGET_COST"
    INSTALLS_NO_TARGET = "INSTALLS_NO_TARGET"
    IN_APP_ACTIONS_TARGET_COST = "IN_APP_ACTIONS_TARGET_COST"


class AppStore(str, Enum):
    GOOGLE_APP_STORE = "GOOGLE_APP_STORE"
    APPLE_APP_STORE = "APPLE_APP_STORE"


# goal -> (API goal type, GoalKind guarded by the guardrails file or None)
_GOALS = {
    Goal.INSTALLS_TARGET_COST: (
        "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
        GoalKind.INSTALL,
    ),
    Goal.INSTALLS_NO_TARGET: (
        "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST",
        None,
    ),
    Goal.IN_APP_ACTIONS_TARGET_COST: (
        "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
        GoalKind.ACTION,
    ),
}
UNIT = {GoalKind.INSTALL: "per install", GoalKind.ACTION: "per in-app action"}


def to_micros(amount, name: str) -> int:
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not math.isfinite(amount)
        or amount <= 0
    ):
        raise ToolError(f"Error: '{name}' must be a finite number > 0.")
    return round(amount * 1_000_000)


def _check_list(items, name: str, lo: int, hi: int) -> None:
    if items is None:
        return
    if not lo <= len(items) <= hi:
        raise ToolError(
            f"Error: '{name}' needs {lo}-{hi} items, got {len(items)}."
        )
    if any(not str(i).strip() for i in items):
        raise ToolError(f"Error: '{name}' must not contain blank values.")
    if len(set(items)) != len(items):
        raise ToolError(f"Error: '{name}' must not contain duplicates.")


def validate_ad_texts(
    headlines: list[str] | None,
    descriptions: list[str] | None,
    image_assets: list[str] | None,
    youtube_video_assets: list[str] | None,
) -> None:
    """Refuse ad content the API would reject. A None list is skipped."""
    _check_list(headlines, "headlines", 2, 5)
    _check_list(descriptions, "descriptions", 1, 5)
    _check_list(image_assets, "image_assets", 0, 20)
    _check_list(youtube_video_assets, "youtube_video_assets", 0, 20)


def resource_name(value, cid: str, collection: str) -> str:
    value = str(value).strip()
    if "/" in value:
        return value
    return f"customers/{cid}/{collection}/{value}"


def _constants(ids, collection: str) -> list[str]:
    return [f"{collection}/{str(i).strip()}" for i in ids or []]


@app_campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def create_app_campaign(
    customer_id: str | int,
    name: str,
    app_id: str,
    daily_budget: float,
    contains_eu_political_advertising: bool,
    goal: Goal,
    headlines: list[str],
    descriptions: list[str],
    target_cost: float | None = None,
    app_store: AppStore = AppStore.GOOGLE_APP_STORE,
    conversion_actions: list[str] | None = None,
    location_ids: list[str | int] | None = None,
    language_ids: list[str | int] | None = None,
    image_assets: list[str] | None = None,
    youtube_video_assets: list[str] | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a complete App campaign (budget, campaign, targeting, ad group
    and ad) in one atomic request. The campaign is always created PAUSED.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call `create_app_campaign` with `validate_only=True` (the default). After validation passes, the tool returns structured `change_items` and a `checksum` without creating anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call `create_app_campaign` with `validate_only=False`, the exact same parameters and the `checksum` returned by the previous call.

    `daily_budget` must be the amount the user stated, in account currency
    units. Never suggest, round or invent a budget yourself. Target costs are
    checked against the user's guardrails file and refused if outside it.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        name: Campaign name.
        app_id: The app's package name (Google Play) or numeric ID (App Store).
        daily_budget: Daily budget in account currency units (e.g. 5.00). Required, no default.
        contains_eu_political_advertising: Whether the campaign has EU political advertising. Required, no default.
        goal: INSTALLS_TARGET_COST, INSTALLS_NO_TARGET or IN_APP_ACTIONS_TARGET_COST.
        headlines: 2-5 headlines.
        descriptions: 1-5 descriptions.
        target_cost: Target cost per install or per in-app action, in account currency units. Required for the two target goals; not allowed for INSTALLS_NO_TARGET.
        app_store: GOOGLE_APP_STORE (default) or APPLE_APP_STORE.
        conversion_actions: Conversion action IDs or resource names. Required for IN_APP_ACTIONS_TARGET_COST.
        location_ids: Geo target constant IDs, e.g. 2840 for the United States.
        language_ids: Language constant IDs, e.g. 1000 for English.
        image_assets: Up to 20 image asset IDs or resource names.
        youtube_video_assets: Up to 20 video asset IDs or resource names.
        login_customer_id: Optional manager customer ID to use as the login-customer-id header.
        validate_only: If True (default), validates and returns `change_items` and a `checksum`. If False, requires `checksum`.
        checksum: Checksum from a prior `validate_only=True` call. Required when `validate_only=False`.
    """
    goal, app_store = Goal(goal), AppStore(app_store)
    eu = contains_eu_political_advertising
    guardrails.ensure_commit_allowed("create_app_campaign", validate_only)
    cid = utils.clean_customer_id(customer_id)
    if not cid:
        raise ToolError("Error: 'customer_id' must contain digits.")
    if not name.strip():
        raise ToolError("Error: 'name' must not be blank.")
    if not app_id.strip():
        raise ToolError("Error: 'app_id' must not be blank.")
    budget_micros = to_micros(daily_budget, "daily_budget")
    validate_ad_texts(
        headlines, descriptions, image_assets, youtube_video_assets
    )

    goal_type, kind = _GOALS[goal]
    target_micros = None
    if kind is None:
        if target_cost is not None:
            raise ToolError(
                "Error: 'target_cost' is not allowed for INSTALLS_NO_TARGET."
            )
    else:
        if target_cost is None:
            raise ToolError(
                f"Error: 'target_cost' is required for {goal.value}."
            )
        target_micros = to_micros(target_cost, "target_cost")
        if kind is GoalKind.ACTION and not conversion_actions:
            raise ToolError(
                "Error: 'conversion_actions' is required for "
                "IN_APP_ACTIONS_TARGET_COST."
            )
        try:
            rng = guardrails.load_range(cid, kind)
        except GuardrailError as e:
            raise ToolError(f"Error: {e}") from e
        try:
            guardrails.check_initial_target(rng, target_micros)
        except GuardrailError as e:
            raise ToolError(
                f"Error: 'target_cost' {e}. Guardrails are set by the user "
                "and cannot be overridden."
            ) from e

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(
        client, "GoogleAdsService"
    )
    currency = queries.get_currency_code(client, cid)
    enums = client.enums

    ops = []

    def new_op():
        op = client.get_type("MutateOperation")
        ops.append(op)
        return op

    budget_rn = f"customers/{cid}/campaignBudgets/-1"
    campaign_rn = f"customers/{cid}/campaigns/-2"
    ad_group_rn = f"customers/{cid}/adGroups/-3"

    budget = new_op().campaign_budget_operation.create
    budget.resource_name = budget_rn
    budget.name = f"{name} budget"
    budget.amount_micros = budget_micros
    budget.explicitly_shared = False

    campaign = new_op().campaign_operation.create
    campaign.resource_name = campaign_rn
    campaign.name = name
    campaign.status = enums.CampaignStatusEnum.PAUSED
    campaign.advertising_channel_type = (
        enums.AdvertisingChannelTypeEnum.MULTI_CHANNEL
    )
    campaign.advertising_channel_sub_type = (
        enums.AdvertisingChannelSubTypeEnum.APP_CAMPAIGN
    )
    campaign.campaign_budget = budget_rn
    campaign.contains_eu_political_advertising = (
        enums.EuPoliticalAdvertisingStatusEnum.CONTAINS_EU_POLITICAL_ADVERTISING
        if eu
        else enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    setting = campaign.app_campaign_setting
    setting.bidding_strategy_goal_type = getattr(
        enums.AppCampaignBiddingStrategyGoalTypeEnum, goal_type
    )
    setting.app_id = app_id.strip()
    setting.app_store = getattr(enums.AppCampaignAppStoreEnum, app_store.value)
    if target_micros is None:
        # An empty message does not mark the oneof; SetInParent does.
        campaign._pb.maximize_conversions.SetInParent()
    else:
        campaign.target_cpa.target_cpa_micros = target_micros
    for ca in conversion_actions or []:
        campaign.selective_optimization.conversion_actions.append(
            resource_name(ca, cid, "conversionActions")
        )

    for rn in _constants(location_ids, "geoTargetConstants"):
        crit = new_op().campaign_criterion_operation.create
        crit.campaign = campaign_rn
        crit.location.geo_target_constant = rn
    for rn in _constants(language_ids, "languageConstants"):
        crit = new_op().campaign_criterion_operation.create
        crit.campaign = campaign_rn
        crit.language.language_constant = rn

    ad_group = new_op().ad_group_operation.create
    ad_group.resource_name = ad_group_rn
    ad_group.campaign = campaign_rn
    ad_group.name = f"{name} ad group"
    ad_group.status = enums.AdGroupStatusEnum.ENABLED

    ad_group_ad = new_op().ad_group_ad_operation.create
    ad_group_ad.ad_group = ad_group_rn
    ad_group_ad.status = enums.AdGroupAdStatusEnum.ENABLED
    app_ad = ad_group_ad.ad.app_ad
    for texts, field, type_name in (
        (headlines, app_ad.headlines, "AdTextAsset"),
        (descriptions, app_ad.descriptions, "AdTextAsset"),
    ):
        for text in texts:
            a = client.get_type(type_name)
            a.text = text
            field.append(a)
    for items, field, type_name, collection in (
        (image_assets, app_ad.images, "AdImageAsset", "assets"),
        (youtube_video_assets, app_ad.youtube_videos, "AdVideoAsset", "assets"),
    ):
        for item in items or []:
            a = client.get_type(type_name)
            a.asset = resource_name(item, cid, collection)
            field.append(a)

    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = cid
    request.mutate_operations.extend(ops)
    request.validate_only = validate_only

    if kind is None:
        target_text = "none (Google sets the cost)"
    else:
        target_text = f"{target_micros / 1e6:.2f} {currency} {UNIT[kind]}"
    change_items = pac.build_change_items(
        "CREATE",
        "App campaign",
        name,
        {
            "Daily budget": f"{budget_micros / 1e6:.2f} {currency}",
            "Target cost": target_text,
            "Conversion actions": [
                resource_name(ca, cid, "conversionActions")
                for ca in conversion_actions or []
            ]
            or "none (Google's default install tracking)",
            "Status": "PAUSED",
            "App": app_id.strip(),
            "App store": app_store.value,
            "EU political advertising": (
                "Contains EU political ads" if eu else "No EU political ads"
            ),
            "Locations": ", ".join(map(str, location_ids or []))
            or "all (no location targeting)",
            "Languages": ", ".join(map(str, language_ids or []))
            or "all (no language targeting)",
            "Headlines": headlines,
            "Descriptions": descriptions,
            "Image assets": [
                resource_name(i, cid, "assets") for i in image_assets or []
            ]
            or "none",
            "Video assets": [
                resource_name(v, cid, "assets")
                for v in youtube_video_assets or []
            ]
            or "none",
        },
    )
    operation_payload = {
        "tool": "create_app_campaign",
        "customer_id": cid,
        "login_customer_id": (
            utils.clean_customer_id(login_customer_id)
            if login_customer_id is not None
            else None
        ),
        "name": name,
        "app_id": app_id,
        "daily_budget": daily_budget,
        "contains_eu_political_advertising": eu,
        "goal": goal.value,
        "headlines": headlines,
        "descriptions": descriptions,
        "target_cost": target_cost,
        "app_store": app_store.value,
        "conversion_actions": conversion_actions,
        "location_ids": [str(i) for i in location_ids or []],
        "language_ids": [str(i) for i in language_ids or []],
        "image_assets": image_assets,
        "youtube_video_assets": youtube_video_assets,
    }

    def _validate() -> None:
        request.validate_only = True
        service.mutate(request=request)

    def _commit() -> str:
        request.validate_only = False
        response = service.mutate(request=request)
        new_campaign = response.mutate_operation_responses[
            1
        ].campaign_result.resource_name
        return (
            f"Successfully created PAUSED App campaign. "
            f"ID: {utils.extract_id(new_campaign)}"
        )

    return pac.propose_and_confirm(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to create App campaign",
    )
