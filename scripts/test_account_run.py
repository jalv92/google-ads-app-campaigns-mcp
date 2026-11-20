#!/usr/bin/env python
"""First real-API run of the App campaign tools, on a Google Ads TEST account.

Drives the five tools through FastMCP's in-process client against
build_server(), the same path an MCP client takes, so the run exercises the
mounted tool names, argument validation, guardrails and propose-and-confirm
flow. Nothing here needs a production account; the script refuses to touch one.

Steps, each a proposal (validate_only=True) followed by a commit with the
returned checksum: create, update headlines, add a location, move the target
inside the guardrails range, enable, pause. Extra checks answer questions the
mocked unit tests cannot: whether the API accepts an empty maximize_conversions
for INSTALLS_NO_TARGET, whether the change-age helper returns a number (it also counts the
campaign's creation, so that alone does not prove the target change was
logged), whether change history has a CAMPAIGN UPDATE event of the target, and
whether each GAQL recipe in the playbook is accepted. A table of every row is
printed at the end, even when a step failed.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from importlib.resources import files

APP_ID = "com.google.android.apps.adwords"  # Google's own sample app
# Google documents up to three minutes for a change to reach change history;
# the first run on a test account saw more than six, so wait much longer.
RETRY_SECONDS = 900
RETRY_EVERY = 30
NEW_LOCATION = 2826  # United Kingdom
REPO_NOTE = "Google Ads test accounts never serve ads."

rows: list[tuple[str, str, str, str]] = []  # step, proposed, committed, message


failures: list[str] = []


def record(step, proposed, committed, message="", failed=False):
    """Add a table row. Any "no"/"NO"/skipped outcome, or failed=True, makes
    the run fail: a run with a skipped row proves less than it claims."""
    if (
        failed
        or proposed == "no"
        or committed in ("no", "NO", "skipped")
        or proposed == "skipped"
    ):
        failures.append(step)
    message = " ".join(str(message).split())
    rows.append((step, proposed, committed, message[:300]))


def print_table() -> None:
    header = ("Step", "PROPOSED ok", "COMMITTED ok", "API message")
    table = [header] + rows
    widths = [max(len(r[i]) for r in table) for i in range(3)]
    print("\n=== Results ===")
    for n, r in enumerate(table):
        print("  ".join(r[i].ljust(widths[i]) for i in range(3)) + "  " + r[3])
        if n == 0:
            print("  ".join("-" * w for w in widths) + "  " + "-" * 11)


def fail(msg: str, code: int = 2):
    print(f"Refusing to run: {msg}", file=sys.stderr)
    raise SystemExit(code)


def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Run the App campaign tools once against a Google Ads TEST "
            "account and print a results table. Refuses to run on an "
            "account whose customer.test_account is not true."
        ),
        epilog=(
            "Needs Application Default Credentials with the adwords scope "
            "and a guardrails file whose install range for the customer has "
            "min_days_between_changes: 0 (the campaign's own creation would "
            "otherwise trip the cooldown)."
        ),
    )
    p.add_argument("--customer-id", required=True, help="test client account")
    p.add_argument(
        "--login-customer-id",
        help="test manager account the client account sits under",
    )
    p.add_argument(
        "--guardrails",
        required=True,
        help="path of the guardrails YAML used for this run",
    )
    return p.parse_args()


def digits(value) -> str:
    return re.sub(r"\D", "", str(value))


def check_environment(args):
    """Everything that can be checked before any API call. Returns the
    target-cost plan (initial, new) in account currency units."""
    from ads_app_mcp import guardrails

    if guardrails.is_unattended():
        fail(
            f"{guardrails.UNATTENDED_ENV} is set. Unattended mode forbids "
            "committing create, enable and targeting changes, which this "
            "run must do. Unset it."
        )
    os.environ[guardrails.GUARDRAILS_ENV] = args.guardrails
    cid = digits(args.customer_id)
    try:
        rng = guardrails.load_range(cid, guardrails.GoalKind.INSTALL)
    except guardrails.GuardrailError as e:
        fail(f"the guardrails file does not cover this run: {e}")
    if rng.min_days_between_changes != 0:
        fail(
            "min_days_between_changes must be 0 for the install range in "
            "this run's guardrails file. The campaign's creation counts as "
            "a target change in the change history, so any larger value "
            "makes set_target_cost refuse right after create_app_campaign. "
            "That refusal is covered by unit tests; this run needs the "
            "target move to go through."
        )
    # Initial target: middle of the range. New target: half a step away, so
    # it stays inside the step limit and the range, and differs in cents.
    lo, hi = rng.min_micros / 1e6, rng.max_micros / 1e6
    initial = round((lo + hi) / 2, 2)
    delta = 0.5 * rng.max_step_pct / 100
    for new in (
        round(initial * (1 + delta), 2),
        round(initial * (1 - delta), 2),
    ):
        if lo <= new <= hi and new != initial:
            return initial, new, cid
    fail(
        f"the install range {lo:.2f}-{hi:.2f} with max_step_pct "
        f"{rng.max_step_pct} leaves no distinct second target to move to."
    )


def check_credentials():
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    try:
        google.auth.default()
    except DefaultCredentialsError as e:
        fail(
            "no Google credentials found. Run: gcloud auth "
            "application-default login --scopes="
            "https://www.googleapis.com/auth/adwords,"
            f"https://www.googleapis.com/auth/cloud-platform ({e})",
            code=3,
        )


def payload(result):
    """The Python value of a tool result."""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        return sc["result"]
    if sc is not None:
        return sc
    return json.loads(result.content[0].text)


def text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


class Runner:
    def __init__(self, client, customer_id, login_customer_id):
        self.c = client
        self.cid = customer_id
        self.login = login_customer_id

    def base(self):
        out = {"customer_id": self.cid}
        if self.login:
            out["login_customer_id"] = self.login
        return out

    async def call(self, tool, **kw):
        return await self.c.call_tool(tool, {**self.base(), **kw})

    async def search(
        self, fields, resource, conditions=(), orderings=(), limit=None
    ):
        kw = {"fields": list(fields), "resource": resource}
        if conditions:
            kw["conditions"] = list(conditions)
        if orderings:
            kw["orderings"] = list(orderings)
        if limit:
            kw["limit"] = limit
        return payload(await self.call("search_search", **kw))

    async def step(self, label, tool, commit=True, **kw):
        """Proposal, then (optionally) commit with its checksum. Records one
        row; returns the commit text, or None when anything failed."""
        print(f"\n--- {label} ---")
        try:
            res = await self.call(tool, validate_only=True, **kw)
            proposal = json.loads(text(res))
        except Exception as e:
            print(f"proposal failed: {e}")
            record(label, "no", "skipped", e)
            return None
        print(json.dumps(proposal["change_items"], indent=2))
        if not commit:
            record(label, "yes", "n/a (proposal only)", "validate_only passed")
            return ""
        try:
            res = await self.call(
                tool, validate_only=False, checksum=proposal["checksum"], **kw
            )
        except Exception as e:
            print(f"commit failed: {e}")
            record(label, "yes", "no", e)
            return None
        out = text(res)
        print(out)
        record(label, "yes", "yes", out)
        return out


def recipes() -> list[tuple[str, str]]:
    """The GAQL code blocks of the playbook, as (title, query)."""
    md = files("ads_app_mcp").joinpath("playbook.md").read_text("utf-8")
    found = re.findall(
        r"^### (\d)\. (.+?)\n.*?```sql\n(.*?)```", md, re.S | re.M
    )
    return [(f"{n}. {t}", " ".join(q.split())) for n, t, q in found]


def split_query(query: str):
    m = re.fullmatch(
        r"SELECT (.+?) FROM (\w+)(?: WHERE (.+?))?"
        r"(?: ORDER BY (.+?))?(?: LIMIT (\d+))?",
        query,
    )
    if not m:
        raise ValueError(f"cannot split query: {query}")
    fields, resource, where, order, limit = m.groups()
    return (
        [f.strip() for f in fields.split(",")],
        resource,
        where.split(" AND ") if where else [],
        [o.strip() for o in order.split(",")] if order else [],
        int(limit) if limit else None,
    )


async def run_recipes(r: Runner, campaign_id):
    found = recipes()
    if len(found) != 8:
        record(
            "GAQL recipes",
            "n/a",
            "n/a",
            f"expected 8 recipes, parsed {len(found)}",
            failed=True,
        )
    for title, query in found:
        ranges = (
            ["YESTERDAY", "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS"]
            if "{date_range}" in query
            else [None]
        )
        problems, nrows, own = [], 0, []
        for dr in ranges:
            q = query.replace("{campaign_id}", str(campaign_id))
            q = q.replace("{start_date}", _utc_day(-14)).replace(
                "{today}", _utc_day(0)
            )
            if dr:
                q = q.replace("{date_range}", dr)
            try:
                parts = split_query(q)
            except ValueError as e:
                own.append(str(e))
                continue
            try:
                out = await r.search(*parts)
                nrows += len(out)
            except Exception as e:
                problems.append(f"{dr or ''} {e}".strip())
        name = f"GAQL recipe {title}"
        if own:
            print(f"{name}: script could not parse the recipe: {own[0]}")
            record(
                name,
                "n/a",
                "n/a",
                "script parsing failure, query not sent: " + own[0],
                failed=True,
            )
        elif problems:
            print(f"{name}: REJECTED {problems[0]}")
            record(
                name,
                "n/a",
                "n/a",
                "API rejected: " + problems[0],
                failed=True,
            )
        else:
            print(f"{name}: accepted, {nrows} rows")
            record(name, "n/a", "n/a", f"API accepted the query ({nrows} rows)")


def check_change_history(customer_id, login, campaign_id):
    import ads_mcp.utils as utils

    from ads_app_mcp import queries

    client = utils.get_googleads_client(login_customer_id=login)
    info = queries.get_app_campaign(client, customer_id, campaign_id)
    deadline = time.monotonic() + RETRY_SECONDS
    last = None
    while True:
        try:
            days = queries.days_since_target_change(
                client, customer_id, campaign_id, info.time_zone
            )
            last = f"returned {days!r}"
            if days is not None:
                return True, f"change history reports {days:.4f} days"
        except Exception as e:
            last = f"raised {e}"
        if time.monotonic() >= deadline:
            return False, f"still not a number after {RETRY_SECONDS}s ({last})"
        print(f"change history not ready ({last}); retrying")
        time.sleep(RETRY_EVERY)


def _utc_day(offset: int) -> str:
    """A calendar date `offset` days from today (UTC), as YYYY-MM-DD."""
    from datetime import datetime, timedelta, timezone

    day = datetime.now(timezone.utc) + timedelta(days=offset)
    return day.strftime("%Y-%m-%d")


def _paths(value) -> list[str]:
    """changed_fields as search_search returns it, as snake_case paths.

    Live, upstream's search tool returns the field mask as ONE comma-joined
    string of camelCase paths ("status,appAd.headlines"); a mask
    dict or a list is accepted too.
    """
    if isinstance(value, dict):
        value = value.get("paths", [])
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [
        re.sub(r"(?<!^)(?<![._])(?=[A-Z])", "_", str(p).strip()).lower()
        for p in value
    ]


async def target_change_logged(r: Runner, campaign_id):
    """Whether change history has a CAMPAIGN UPDATE event that changes the
    target cost, retrying while the history lags."""
    from ads_app_mcp import queries

    deadline = time.monotonic() + RETRY_SECONDS
    while True:
        try:
            events = await r.search(
                [
                    "change_event.change_date_time",
                    "change_event.change_resource_type",
                    "change_event.resource_change_operation",
                    "change_event.changed_fields",
                ],
                "change_event",
                [
                    f"campaign.id = {campaign_id}",
                    # LAST_N_DAYS ends yesterday, so name the days: a day
                    # either side of UTC today covers every account time zone.
                    f"change_event.change_date_time >= '{_utc_day(-2)}'",
                    "change_event.change_date_time <= "
                    f"'{_utc_day(1)} 23:59:59'",
                ],
                ["change_event.change_date_time DESC"],
                100,
            )
            for e in events:
                if (
                    e.get("change_event.change_resource_type") == "CAMPAIGN"
                    and "UPDATE"
                    in str(e.get("change_event.resource_change_operation"))
                    and queries.touches_target(
                        _paths(e.get("change_event.changed_fields"))
                    )
                ):
                    return True, "a CAMPAIGN UPDATE event changes the target"
            last = f"{len(events)} events, none a CAMPAIGN UPDATE of the target"
        except Exception as ex:
            last = f"query failed: {ex}"
        if time.monotonic() >= deadline:
            return False, f"not found after {RETRY_SECONDS}s ({last})"
        print(f"target change not in change history yet ({last}); retrying")
        await asyncio.sleep(RETRY_EVERY)


async def main_async(args, initial, new_target, cid):
    from fastmcp import Client

    from ads_app_mcp.server import build_server

    async with Client(build_server()) as client:
        r = Runner(client, cid, digits(args.login_customer_id) or None)

        # Safety gate: nothing else happens unless this is a test account.
        try:
            res = await r.search(["customer.test_account"], "customer")
            is_test = bool(res and res[0].get("customer.test_account") is True)
        except Exception as e:
            fail(f"could not read customer.test_account: {e}", code=4)
        if not is_test:
            fail(
                f"customer {cid} is not a test account "
                "(customer.test_account is not true). This script only runs "
                "on Google Ads test accounts.",
                code=4,
            )
        print(f"customer {cid} is a test account; {REPO_NOTE}")
        record(
            "customer.test_account is true",
            "n/a",
            "n/a",
            "read through search_search",
        )

        common = dict(
            app_id=APP_ID,
            daily_budget=1.00,
            contains_eu_political_advertising=False,
            location_ids=[2840],
            language_ids=[1000],
        )
        stamp = time.strftime("%Y%m%d-%H%M%S")
        await r.step(
            "proposal only: INSTALLS_NO_TARGET (empty maximize_conversions)",
            "app_campaigns_create_app_campaign",
            commit=False,
            name=f"MCP test no-target {stamp}",
            goal="INSTALLS_NO_TARGET",
            headlines=["Manage your ads", "Track campaigns"],
            descriptions=["Check results anywhere"],
            **common,
        )

        out = await r.step(
            "create (PAUSED, INSTALLS_TARGET_COST)",
            "app_campaigns_create_app_campaign",
            name=f"MCP test {stamp}",
            goal="INSTALLS_TARGET_COST",
            target_cost=initial,
            headlines=["Manage your ads", "Track campaigns"],
            descriptions=["Check results anywhere"],
            **common,
        )
        m = re.search(r"ID: (\d+)", out or "")
        if not m:
            if out is None:
                why = "create failed, so there is no campaign"
            else:
                why = "create committed but its result has no campaign ID"
                rows[-1] = (
                    rows[-1][0],
                    "yes",
                    "NO",
                    "created, but no ID in the result: find the paused "
                    "campaign in the account",
                )
                failures.append(rows[-1][0])
            for s in (
                "update headlines",
                "add a location",
                "set_target_cost",
                "days_since_target_change returns a number",
                "change_event logs the target change",
                "enable",
                "pause",
                "GAQL recipes",
            ):
                record(s, "skipped", "skipped", why)
            return
        camp = m.group(1)

        await r.step(
            "update headlines",
            "app_campaigns_update_app_ad",
            campaign_id=camp,
            headlines=[
                "Manage your ads",
                "Track campaigns",
                "Ads in your pocket",
            ],
        )
        await r.step(
            f"add a location ({NEW_LOCATION})",
            "app_campaigns_set_campaign_targeting",
            campaign_id=camp,
            add_location_ids=[NEW_LOCATION],
        )
        moved = await r.step(
            f"set_target_cost {initial:.2f} -> {new_target:.2f}",
            "app_campaigns_set_target_cost",
            campaign_id=camp,
            target_cost=new_target,
        )
        if moved is None:
            for s in (
                "days_since_target_change returns a number",
                "change_event logs the target change",
            ):
                record(s, "skipped", "skipped", "target change did not commit")
        else:
            ok, msg = await asyncio.to_thread(
                check_change_history, cid, r.login, camp
            )
            record(
                "days_since_target_change returns a number",
                "n/a",
                "yes" if ok else "NO",
                msg,
            )
            ok, msg = await target_change_logged(r, camp)
            record(
                "change_event logs the target change",
                "n/a",
                "yes" if ok else "NO",
                msg,
            )
        await r.step(
            "enable",
            "app_campaigns_set_campaign_status",
            campaign_id=camp,
            status="ENABLED",
        )
        await r.step(
            "pause",
            "app_campaigns_set_campaign_status",
            campaign_id=camp,
            status="PAUSED",
        )
        await run_recipes(r, camp)
        print(f"\nCampaign left PAUSED in the test account: {camp}")


def main() -> int:
    args = parse_args()
    initial, new_target, cid = check_environment(args)
    check_credentials()
    try:
        asyncio.run(main_async(args, initial, new_target, cid))
    except SystemExit:
        raise
    except BaseException as e:  # keep the table even on a surprise
        record("run aborted", "n/a", "n/a", repr(e), failed=True)
    finally:
        if rows:
            print_table()
    if failures:
        print(f"\nFAILED rows: {len(failures)}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
