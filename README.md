<div align="center">

<h1>google-ads-app-campaigns-mcp</h1>

<p>
  <b>App campaign tools for Google's official Google Ads MCP server, for people who want an AI agent to run their app's ads without ever touching the budget.</b><br>
  It creates, pauses, retargets and re-words App campaigns, moves the target cost only inside a range you wrote, and has no tool that changes a budget at all.
</p>

<p>
  <a href="#quick-start">Quick start</a> ·
  <a href="#the-money-boundary">The money boundary</a> ·
  <a href="#guardrails-file">Guardrails</a> ·
  <a href="#daily-review">Daily review</a> ·
  <a href="#status">Status</a>
</p>

<p>
  <img src="https://img.shields.io/badge/status-pre--release-orange?style=flat-square" alt="">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square" alt="">
  <img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="">
  <img src="https://img.shields.io/badge/google--ads--mcp-0.0.6%20pinned-4285f4?style=flat-square" alt="">
</p>

</div>

```text
# the five tools this package mounts, listed by an MCP client:
app_campaigns_create_app_campaign
app_campaigns_update_app_ad
app_campaigns_set_campaign_status
app_campaigns_set_campaign_targeting
app_campaigns_set_target_cost

# create_app_campaign(goal="INSTALLS_TARGET_COST", target_cost=3.00, ...)
# with the guardrails file allowing 0.80-1.50 per install:
ToolError: Error: 'target_cost' 3.00 is outside the range the user allows (0.80-1.50). Guardrails are set by the user and cannot be overridden.
```

Both outputs above come from the real server, called through FastMCP's in-process client. The
refusal needs no Google credentials: the server checks your limits before it contacts Google.

---

## What it adds

Google's official [Google Ads MCP server](https://github.com/googleads/google-ads-mcp) reads any
account with GAQL and manages assets and labels. It cannot create a campaign, change its status,
its bid target, its ad or its targeting. This package installs that server as a dependency and
mounts five more tools next to its own, as one MCP server.

| Tool | What it does |
|---|---|
| `app_campaigns_create_app_campaign` | Budget, campaign, targeting, ad group and ad in one atomic request. Always created PAUSED. |
| `app_campaigns_set_campaign_status` | Pauses or enables a campaign. No remove. |
| `app_campaigns_set_target_cost` | Changes the target cost per install or per in-app action, inside your limits. |
| `app_campaigns_update_app_ad` | Replaces the ad's headlines, descriptions, images or videos in place. |
| `app_campaigns_set_campaign_targeting` | Adds and removes locations and languages. |

It also serves a strategy playbook as the resource `resource://app-campaign-playbook` (the
queries that read an App campaign and the rules for what an agent may change, each tied to the
Google page it comes from or marked as an adjustable default) and a `daily_review` prompt that
runs it. There are no read tools: statistics come from the official server's `search_search`.

Every write is propose-and-confirm: called with `validate_only=True` (the default) a tool checks
the change with Google and returns what it would do plus a checksum; it commits only when called
again with the same arguments, `validate_only=False` and that checksum. Scope is App campaigns of
the `APP_CAMPAIGN` sub-type with install and in-app-action goals; any other campaign type is
refused.

## The money boundary

Read this section before you trust it with an account.

- **Budget: no tool can change it.** There is no budget tool. The daily budget is typed by you
  when a campaign is created, shown in the proposal, and never touched again by anything in this
  package. A test fails if any tool other than the campaign-creating one builds a budget
  operation. Raising or lowering a budget stays a decision you make in Google Ads.
- **Target cost: only inside your limits.** `set_target_cost` reads a YAML file you own (see
  [Guardrails file](#guardrails-file)) and refuses a value outside your range, a step bigger than
  you allow, and a change too soon after the last one. If the file is missing, does not mention
  the account, or Google's change history cannot be read, it refuses. A campaign cannot even be
  created with a target outside the range.
- **Unattended mode: only two tools may commit.** With `GOOGLE_ADS_APP_MCP_UNATTENDED` set to
  any value other than empty, `0`, `false` or `no` (in any case), only `set_target_cost` and
  `update_app_ad` can commit. An unrecognised value counts as unattended. Creating, enabling or
  pausing a campaign and changing its targeting can still be proposed, but a commit is refused.
  This is what makes "a scheduled run never opens a new country or turns a campaign on" a rule
  of the server instead of a request in a prompt. It cannot tell a text change from an image change
  inside `update_app_ad`, and it does not gate the official server's own asset and label tools;
  the playbook asks the agent to propose image and video changes in an unattended run.
- **What none of this protects against.** These checks stop an agent's mistakes and drift. They
  are not a security boundary against a hostile process. Whatever runs on your machine with a
  shell can read your Google credentials and call the Google Ads API directly, skipping this
  server and your guardrails file. The real backstops are the daily budget, which only you set,
  and keeping the credentials where only this server can reach them. For stronger isolation, run
  the server where the agent has no shell access to the credentials or to the guardrails file.

## Quick start

The package is not on PyPI yet; a release will follow. Until then run it from the repository.
You need [pipx](https://pipx.pypa.io/stable/#install-pipx) and the one-time Google setup below.

```shell
pipx run --spec git+https://github.com/jalv92/google-ads-app-campaigns-mcp.git google-ads-app-campaigns-mcp
```

Your MCP client starts the server itself. The block below is the standard `mcpServers` format
shared by MCP clients (the same shape the official server documents); put it wherever your client
reads its server list.

```json
{
  "mcpServers": {
    "google-ads-app-campaigns": {
      "command": "pipx",
      "args": [
        "run",
        "--spec",
        "git+https://github.com/jalv92/google-ads-app-campaigns-mcp.git",
        "google-ads-app-campaigns-mcp"
      ],
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "PATH_TO_CREDENTIALS_JSON",
        "GOOGLE_PROJECT_ID": "YOUR_PROJECT_ID",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "YOUR_MANAGER_CUSTOMER_ID",
        "GOOGLE_ADS_APP_MCP_GUARDRAILS": "PATH_TO_YOUR_GUARDRAILS_YAML"
      }
    }
  }
}
```

`GOOGLE_ADS_LOGIN_CUSTOMER_ID` is only for access through a manager account; every tool also
takes a `login_customer_id` argument. Credentials work as in the official server, which also
supports its OAuth proxy and HTTP mode untouched.

### One-time Google setup

1. **Google Cloud project.** Create one, or use an existing one.
2. **Enable the [Google Ads API](https://console.cloud.google.com/apis/library/googleads.googleapis.com)**
   in it. That gives the project Test access: test accounts only.
3. **Explorer access** for real accounts. Developer tokens were retired on 2026-09-09; the
   access level now belongs to the Cloud project. Explorer is applied for in the Cloud Console,
   is approved automatically in most cases, and allows 2,880 operations a day on production
   accounts, which is plenty for a few campaigns. See
   [access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).
   Without it a production account answers
   `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`.
4. **OAuth client** (Desktop app) in the project, downloaded as a JSON file.
5. **Log in with Application Default Credentials**, as the Google user that has access to the
   Google Ads account:

   ```shell
   gcloud auth application-default login \
     --scopes https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/cloud-platform \
     --client-id-file=YOUR_CLIENT_JSON_FILE
   ```

   gcloud prints the credentials file path; that is `PATH_TO_CREDENTIALS_JSON` above.
6. **Link your app to Google Ads** (Google Play or Firebase) in Google's consoles, so install
   conversions exist. This package does not create conversion actions or links.

The server adds a usage-telemetry header to its Google Ads API calls. That is the official
server's behaviour, which this package inherits (their README: "to help us collect usage data,
you will notice an extra header has been added to your API calls").

## Guardrails file

A YAML file you own, kept outside any repository, with its path in
`GOOGLE_ADS_APP_MCP_GUARDRAILS`. Copy `guardrails.example.yaml` and edit it. Values are in
account currency units, not micros. It is re-read on every call.

```yaml
customers:
  "1234567890":              # Google Ads customer ID, digits only
    defaults:
      install:               # campaigns that optimise for installs
        min: 0.80            # lowest target cost the agent may set
        max: 1.50            # highest target cost the agent may set
        max_step_pct: 20     # largest single change, percent of current target (greater than 0, at most 100)
        min_days_between_changes: 3   # wait at least this many days (0-28)
      action:                # campaigns that optimise for in-app actions
        min: 2.00
        max: 6.00
        max_step_pct: 20
        min_days_between_changes: 3
    campaigns:               # optional per-campaign override (all four keys required)
      "111":
        min: 0.90
        max: 1.20
        max_step_pct: 15
        min_days_between_changes: 5
```

Anything missing or invalid makes the server refuse the change. The waiting time counts any
change to the target in Google's change history, whoever made it, and is capped at 28 days
because the history keeps about 30. Creating the campaign counts as a change too. Days are
whole calendar days in the account's time zone: a change counts from the end of the day it was
made, so a wait of N days means between N and N+1 days.

Google's change history is slow to show a change (Google documents up to three minutes; this
project's test-account run saw more than six), so the server also remembers,
in memory, each target change it committed itself (a campaign's creation is only seen once it
shows in the history). A second server process started inside those minutes does not know
about the first one's change.

## Daily review

The `daily_review` prompt takes a `customer_id` and optionally a `campaign_id`. It tells the
agent to run the playbook's queries, make the small changes the playbook and your guardrails
allow, and write a plain-language report: how the campaign did, what changed and why, proposals
waiting for you, and a budget note as text only when the budget is the limit.

For a scheduled run, give the server the unattended flag and your guardrails file:

```json
{
  "mcpServers": {
    "google-ads-app-campaigns": {
      "command": "pipx",
      "args": [
        "run",
        "--spec",
        "git+https://github.com/jalv92/google-ads-app-campaigns-mcp.git",
        "google-ads-app-campaigns-mcp"
      ],
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "PATH_TO_CREDENTIALS_JSON",
        "GOOGLE_PROJECT_ID": "YOUR_PROJECT_ID",
        "GOOGLE_ADS_APP_MCP_GUARDRAILS": "PATH_TO_YOUR_GUARDRAILS_YAML",
        "GOOGLE_ADS_APP_MCP_UNATTENDED": "1"
      }
    }
  }
}
```

Then schedule your MCP client, with whatever scheduler it offers, to run the `daily_review`
prompt once a day with your customer ID. This package ships the prompt and the server, not a
scheduler. Google's change history (`change_event`) is the record of what was changed; the
official server's audit log is not in the pinned 0.0.6 release.

## Status

Pre-release. Every tool is covered by unit tests against a mocked Google Ads API (the budget
invariant, the guardrail edges, the unattended-mode refusals and the golden list of mounted
tools). **All five tools have run against the real Google Ads API on a test account** (2026-11-20:
create, ad update, targeting, target cost, enable, pause, and a cooldown refusal), and the API
accepts all eight playbook queries; the table and what the run changed are in
`docs/test-account-run.md`. **No campaign has run on a real, serving account yet.** A test
account never serves ads, so nothing that depends on metrics, asset ratings or the learning
status has been exercised: treat the playbook's decision rules as untested with real data.

The official server is pinned to exactly 0.0.6 because this package builds on its internals;
the pin is bumped on purpose, after the new version's tools are read for anything that can touch
a budget or a bid. v1 does not cover Search, Performance Max, Display, Video or Shopping
campaigns, the engagement and pre-registration App sub-types, or value goals.

## Development

```shell
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q
```

## License

Apache-2.0, see `LICENSE` and `NOTICE`. This project builds on
[googleads/google-ads-mcp](https://github.com/googleads/google-ads-mcp) (Apache-2.0, Copyright
Google LLC). It is not affiliated with or endorsed by Google.
