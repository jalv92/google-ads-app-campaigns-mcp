"""The strategy playbook resource and the daily_review prompt."""

from importlib.resources import files

from ads_mcp.coordinator import mcp

from ads_app_mcp import guardrails

PLAYBOOK_URI = "resource://app-campaign-playbook"


def _playbook() -> str:
    return files("ads_app_mcp").joinpath("playbook.md").read_text("utf-8")


@mcp.resource(uri=PLAYBOOK_URI, mime_type="text/markdown")
def get_app_campaign_playbook() -> str:
    """How to read a Google Ads App campaign and what an agent may change.

    Holds the queries to run with the `search_search` tool, the decision rules with
    the Google page behind each number, what may be done without the user,
    and the daily report format. Read it before changing an App campaign.
    """
    return _playbook()


_PROMPT = """\
You are the daily reviewer of a Google Ads account's App campaigns. The person \
who owns the account is not a marketer. They want their campaigns to get a \
little better every day, and they keep control of money: you never change a \
budget, and every limit a tool enforces is final.

<review>
customer_id: {customer_id}
scope: {scope}
</review>

{who}

Your rules are the App campaign playbook ({uri}), copied in full at the end of \
this message. Follow it over your own judgement.

Do this:
1. {first_step}
2. For each campaign, run every recipe in "How to read an App campaign" with \
the `search_search` tool, passing customer_id {customer_id}.
3. Go through "Decision rules" in order and decide, for each campaign: change \
nothing, change the target cost, add or replace one ad text, or propose \
something to the user.
4. Act only as "What the agent may do alone and what waits for the user" \
allows, in the column for this run. Every change starts with \
validate_only=True.
5. Answer with the report in "Report format" and nothing else. Write it in the \
language of the request that started this run; if you cannot tell, in English.

When something goes wrong:
- A refusal from a tool's guardrails (outside the user's range, a step too \
big, too soon after the last change, a commit not allowed in an unattended \
run, limits or change history that cannot be read) is final. Do not retry \
with another value, a smaller step, another tool or another campaign to get \
the same effect. Report it in plain words under "Proposals waiting for you".
- If a query fails, write "not available" for the numbers \
it would have given, and make no change that depends on them. Never estimate \
a number you could not read.
- If the playbook does not cover a situation, change nothing and describe the \
situation in the report.

<playbook uri="{uri}">
{playbook}
</playbook>

Now review {scope} of customer {customer_id} as described above, and answer \
with the report.
"""


@mcp.prompt
def daily_review(customer_id: str, campaign_id: str | None = None) -> str:
    """Daily review of App campaigns: read the numbers, make the small changes
    the playbook allows inside the user's limits, and write a plain-language
    report. Reviews one campaign, or every enabled App campaign of the
    customer when campaign_id is left out."""
    if campaign_id:
        scope = f"campaign {campaign_id}"
        first_step = f"Review {scope} only."
    else:
        scope = "every ENABLED App campaign"
        first_step = (
            'Run the playbook recipe "Find the App campaigns" and review each '
            "campaign it returns. If it returns none, say so in the report "
            "and stop."
        )
    if guardrails.is_unattended():
        who = (
            "This run is unattended: nobody is watching. Commit only what the "
            '"Run with nobody watching" column of the playbook allows; '
            "everything else is a proposal in the report."
        )
    else:
        who = (
            "A user is in the conversation. Show each proposal and wait for "
            "their yes before you commit it."
        )
    return _PROMPT.format(
        customer_id=customer_id,
        scope=scope,
        who=who,
        first_step=first_step,
        uri=PLAYBOOK_URI,
        playbook=_playbook(),
    )
