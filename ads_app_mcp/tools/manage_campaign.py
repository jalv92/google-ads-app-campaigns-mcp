"""Pause or enable an App campaign, and change its locations and languages."""

import time
from enum import Enum

import ads_mcp.utils as utils
from ads_mcp import propose_and_confirm as pac
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_app_mcp import guardrails, queries
from ads_app_mcp.guardrails import GuardrailError
from ads_app_mcp.tools import app_campaigns_mcp
from ads_app_mcp.tools.create_campaign import UNIT, to_micros

# (customer_id, campaign_id) -> time.monotonic() of the last target change
# this process committed. Google's change history lags by minutes, so the
# cooldown also counts these. Not persisted: another process does not see them.
# ponytail: no lock, so two parallel calls on one campaign can both pass the
# check; add a per-campaign lock if a client ever runs tools concurrently.
_target_commits: dict[tuple[str, str], float] = {}


class CampaignStatus(str, Enum):
    ENABLED = "ENABLED"
    PAUSED = "PAUSED"


def _customer(customer_id) -> str:
    cid = utils.clean_customer_id(customer_id)
    if not cid:
        raise ToolError("Error: 'customer_id' must contain digits.")
    return cid


def _login(login_customer_id):
    if login_customer_id is None:
        return None
    return utils.clean_customer_id(login_customer_id)


@app_campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def set_campaign_status(
    customer_id: str | int,
    campaign_id: str | int,
    status: CampaignStatus,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Pauses or enables an App campaign. Enabling a campaign starts spending
    its budget, so only enable when the user has said so.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call `set_campaign_status` with `validate_only=True` (the default). After validation passes, the tool returns structured `change_items` and a `checksum` without changing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call `set_campaign_status` with `validate_only=False`, the exact same parameters and the `checksum` returned by the previous call.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: The App campaign ID.
        status: ENABLED or PAUSED.
        login_customer_id: Optional manager customer ID to use as the login-customer-id header.
        validate_only: If True (default), validates and returns `change_items` and a `checksum`. If False, requires `checksum`.
        checksum: Checksum from a prior `validate_only=True` call. Required when `validate_only=False`.
    """
    status = CampaignStatus(status)
    guardrails.ensure_commit_allowed("set_campaign_status", validate_only)
    cid = _customer(customer_id)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    info = queries.get_app_campaign(client, cid, campaign_id)
    if info.status == "REMOVED":
        raise ToolError(f"Error: campaign {info.campaign_id} is removed.")
    if info.status == status.value:
        raise ToolError(
            f"Error: 'status' is already {status.value} for campaign "
            f"{info.campaign_id}."
        )

    service = utils.get_googleads_service_from_client(client, "CampaignService")
    op = client.get_type("CampaignOperation")
    op.update.resource_name = f"customers/{cid}/campaigns/{info.campaign_id}"
    op.update.status = getattr(client.enums.CampaignStatusEnum, status.value)
    op.update_mask.paths.append("status")
    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = cid
    request.operations.append(op)

    change_items = pac.build_change_items(
        "UPDATE",
        "App campaign",
        f"{info.name} ({info.campaign_id})",
        {"Status": status.value},
        {"Status": info.status},
    )
    operation_payload = {
        "tool": "set_campaign_status",
        "customer_id": cid,
        "login_customer_id": _login(login_customer_id),
        "campaign_id": info.campaign_id,
        "status": status.value,
        "current_status": info.status,
    }

    def _validate() -> None:
        request.validate_only = True
        service.mutate_campaigns(request=request)

    def _commit() -> str:
        request.validate_only = False
        service.mutate_campaigns(request=request)
        return f"Campaign {info.campaign_id} is now {status.value}."

    return pac.propose_and_confirm(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to change campaign status",
    )


def _ids(values, name: str) -> list[str]:
    out = []
    for v in values or []:
        s = str(v).strip()
        if not s.isdigit():
            raise ToolError(f"Error: '{name}' must contain numeric IDs.")
        out.append(s)
    if len(set(out)) != len(out):
        raise ToolError(f"Error: '{name}' must not contain duplicates.")
    return out


@app_campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def set_campaign_targeting(
    customer_id: str | int,
    campaign_id: str | int,
    add_location_ids: list[str | int] | None = None,
    remove_location_ids: list[str | int] | None = None,
    add_language_ids: list[str | int] | None = None,
    remove_language_ids: list[str | int] | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Adds and removes the locations and languages an App campaign targets.
    A campaign with no location (or no language) targeting reaches ALL
    locations (or languages); the change items say so when that would happen.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call `set_campaign_targeting` with `validate_only=True` (the default). After validation passes, the tool returns structured `change_items` and a `checksum` without changing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call `set_campaign_targeting` with `validate_only=False`, the exact same parameters and the `checksum` returned by the previous call.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: The App campaign ID.
        add_location_ids: Geo target constant IDs to add, e.g. 2840 for the United States. Must not be targeted already.
        remove_location_ids: Geo target constant IDs to remove. Must be targeted now.
        add_language_ids: Language constant IDs to add, e.g. 1000 for English. Must not be targeted already.
        remove_language_ids: Language constant IDs to remove. Must be targeted now.
        login_customer_id: Optional manager customer ID to use as the login-customer-id header.
        validate_only: If True (default), validates and returns `change_items` and a `checksum`. If False, requires `checksum`.
        checksum: Checksum from a prior `validate_only=True` call. Required when `validate_only=False`.
    """
    guardrails.ensure_commit_allowed("set_campaign_targeting", validate_only)
    cid = _customer(customer_id)
    changes = {
        "location": (
            _ids(add_location_ids, "add_location_ids"),
            _ids(remove_location_ids, "remove_location_ids"),
        ),
        "language": (
            _ids(add_language_ids, "add_language_ids"),
            _ids(remove_language_ids, "remove_language_ids"),
        ),
    }
    if not any(a or r for a, r in changes.values()):
        raise ToolError(
            "Error: nothing to change; give at least one of "
            "'add_location_ids', 'remove_location_ids', 'add_language_ids' "
            "or 'remove_language_ids'."
        )
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    info = queries.get_app_campaign(client, cid, campaign_id)
    current = queries.get_targeting(client, cid, info.campaign_id)

    names = {"location": "location_ids", "language": "language_ids"}
    fields: dict[str, object] = {}
    before: dict[str, object] = {}
    creates, removes = [], []
    for kind, (add, rem) in changes.items():
        have = current[kind]
        if set(add) & set(rem):
            raise ToolError(
                f"Error: 'add_{names[kind]}' and 'remove_{names[kind]}' "
                "must not share IDs."
            )
        for i in add:
            if i in have:
                raise ToolError(
                    f"Error: 'add_{names[kind]}' {i} is already targeted."
                )
        for i in rem:
            if i not in have:
                raise ToolError(
                    f"Error: 'remove_{names[kind]}' {i} is not targeted by "
                    f"campaign {info.campaign_id}."
                )
        if not (add or rem):
            continue
        after = [i for i in have if i not in rem] + add
        label = kind.capitalize() + "s"
        if add:
            fields[f"{label} added"] = ", ".join(add)
        if rem:
            fields[f"{label} removed"] = ", ".join(rem)
        fields[f"{label} after"] = ", ".join(after) or (
            f"all (no {kind} targeting)"
        )
        before[f"{label} after"] = ", ".join(have) or (
            f"all (no {kind} targeting)"
        )
        removes += [have[i] for i in rem]
        creates += [(kind, i) for i in add]

    service = utils.get_googleads_service_from_client(
        client, "CampaignCriterionService"
    )
    campaign_rn = f"customers/{cid}/campaigns/{info.campaign_id}"
    request = client.get_type("MutateCampaignCriteriaRequest")
    request.customer_id = cid
    for rn in removes:
        op = client.get_type("CampaignCriterionOperation")
        op.remove = rn
        request.operations.append(op)
    for kind, i in creates:
        op = client.get_type("CampaignCriterionOperation")
        op.create.campaign = campaign_rn
        if kind == "location":
            op.create.location.geo_target_constant = f"geoTargetConstants/{i}"
        else:
            op.create.language.language_constant = f"languageConstants/{i}"
        request.operations.append(op)

    change_items = pac.build_change_items(
        "UPDATE",
        "App campaign targeting",
        f"{info.name} ({info.campaign_id})",
        fields,
        before,
    )
    operation_payload = {
        "tool": "set_campaign_targeting",
        "customer_id": cid,
        "login_customer_id": _login(login_customer_id),
        "campaign_id": info.campaign_id,
        "add_location_ids": changes["location"][0],
        "remove_location_ids": changes["location"][1],
        "add_language_ids": changes["language"][0],
        "remove_language_ids": changes["language"][1],
        "current_location_ids": sorted(current["location"]),
        "current_language_ids": sorted(current["language"]),
    }

    def _validate() -> None:
        request.validate_only = True
        service.mutate_campaign_criteria(request=request)

    def _commit() -> str:
        request.validate_only = False
        service.mutate_campaign_criteria(request=request)
        return (
            f"Updated targeting of campaign {info.campaign_id}: "
            f"{len(creates)} added, {len(removes)} removed."
        )

    return pac.propose_and_confirm(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to change campaign targeting",
    )


def _refuse(currency: str, why) -> ToolError:
    return ToolError(
        f"Error: 'target_cost' refused: {why}. Amounts are in {currency}. "
        "The limits are set by the user and cannot be overridden."
    )


@app_campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def set_target_cost(
    customer_id: str | int,
    campaign_id: str | int,
    target_cost: float,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Changes the target cost (per install or per in-app action) of an App
    campaign. The change must stay inside the limits in the user's guardrails
    file (range, maximum step, days between changes); anything outside is
    refused and the limits cannot be overridden.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call `set_target_cost` with `validate_only=True` (the default). After validation passes, the tool returns structured `change_items` and a `checksum` without changing anything.
    2. With a user in the conversation, show the `change_items` and wait for a yes. In an unattended run started by the `daily_review` prompt, follow the playbook (`resource://app-campaign-playbook`), which says when this tool may commit on its own.
    3. To commit, call `set_target_cost` with `validate_only=False`, the exact same parameters and the `checksum` returned by the previous call.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: The App campaign ID. It must already have a target cost.
        target_cost: The new target cost in account currency units (e.g. 1.20).
        login_customer_id: Optional manager customer ID to use as the login-customer-id header.
        validate_only: If True (default), validates and returns `change_items` and a `checksum`. If False, requires `checksum`.
        checksum: Checksum from a prior `validate_only=True` call. Required when `validate_only=False`.
    """
    guardrails.ensure_commit_allowed("set_target_cost", validate_only)
    cid = _customer(customer_id)
    new_micros = to_micros(target_cost, "target_cost")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    info = queries.get_app_campaign(client, cid, campaign_id)
    kind = info.goal_kind
    if kind is None or not info.target_cost_micros:
        raise ToolError(
            f"Error: campaign {info.campaign_id} has no target cost to "
            "change; 'target_cost' only applies to campaigns that optimise "
            "for a target cost."
        )
    cur = info.currency_code
    try:
        rng = guardrails.load_range(cid, kind, info.campaign_id)
    except GuardrailError as e:
        raise _refuse(cur, e) from e
    try:
        days = queries.days_since_target_change(
            client, cid, info.campaign_id, info.time_zone
        )
    except Exception as e:
        raise ToolError(
            "Error: the change history could not be read, so the cooldown "
            f"cannot be checked and 'target_cost' is refused ({e})."
        ) from e
    committed = _target_commits.get((cid, info.campaign_id))
    if committed is not None:
        mine = max(0.0, (time.monotonic() - committed) / 86400)
        days = mine if days is None else min(days, mine)
    try:
        guardrails.check_target_change(
            rng, info.target_cost_micros, new_micros, days
        )
    except GuardrailError as e:
        raise _refuse(cur, e) from e

    service = utils.get_googleads_service_from_client(client, "CampaignService")
    op = client.get_type("CampaignOperation")
    op.update.resource_name = f"customers/{cid}/campaigns/{info.campaign_id}"
    op.update.target_cpa.target_cpa_micros = new_micros
    op.update_mask.paths.append("target_cpa.target_cpa_micros")
    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = cid
    request.operations.append(op)

    unit = UNIT[kind]
    change_items = pac.build_change_items(
        "UPDATE",
        "App campaign",
        f"{info.name} ({info.campaign_id})",
        {
            "Target cost": f"{new_micros / 1e6:.2f} {cur} {unit}",
            "Allowed range": (
                f"{rng.min_micros / 1e6:.2f}–{rng.max_micros / 1e6:.2f} "
                f"{cur}, max {rng.max_step_pct:g} % per change, "
                f"{rng.min_days_between_changes} days between changes"
            ),
        },
        {"Target cost": f"{info.target_cost_micros / 1e6:.2f} {cur} {unit}"},
    )
    operation_payload = {
        "tool": "set_target_cost",
        "customer_id": cid,
        "login_customer_id": _login(login_customer_id),
        "campaign_id": info.campaign_id,
        "target_cost": target_cost,
        "current_target_micros": info.target_cost_micros,
    }

    def _validate() -> None:
        request.validate_only = True
        service.mutate_campaigns(request=request)

    def _commit() -> str:
        request.validate_only = False
        service.mutate_campaigns(request=request)
        _target_commits[(cid, info.campaign_id)] = time.monotonic()
        return (
            f"Target cost of campaign {info.campaign_id} is now "
            f"{new_micros / 1e6:.2f} {cur} {unit}."
        )

    return pac.propose_and_confirm(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=operation_payload,
        change_items=change_items,
        validate_fn=_validate,
        commit_fn=_commit,
        error_prefix="Failed to change target cost",
    )
