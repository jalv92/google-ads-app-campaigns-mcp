# Google Ads App Campaigns MCP — Design

Date: 2026-11-20 · Status: approved by the owner on 2026-11-20

## Intent

The owner wants an AI agent (Claude) to run the Google Ads campaign that promotes his app end to end: create it,
read its statistics, and improve it a little every day. The one thing that stays with the human
is money: how much is spent per day. He also wants this published as an open-source MCP server
so other people can do the same with their own apps.

Google's official server (`googleads/google-ads-mcp`, Apache-2.0, v0.0.6) already covers reading
any account data (GAQL), creating assets, labels and a propose-then-confirm safety flow. It cannot create a campaign, change its status, its bid target, its ad or its targeting.
This project adds exactly that gap, for App campaigns, and a playbook that turns the numbers
into daily decisions.

## Goals

1. Create a complete App campaign (budget, campaign, targeting, ad group, ad) from one tool call.
2. Manage it afterwards: pause/enable, change the ad's texts/images/videos, change locations and
   languages, move the target cost inside limits the user set.
3. A daily review that reports performance, applies the changes that fall inside the user's
   limits, and lists the ones that need the user.
4. One install for the user: our package brings the official server's tools plus ours in a
   single MCP server.
5. Runs on the user's own machine with the user's own Google credentials. No hosted service.

## Non-goals (v1)

- Search, Performance Max, Display, Video or Shopping campaigns.
- `APP_CAMPAIGN_FOR_ENGAGEMENT` and `APP_CAMPAIGN_FOR_PRE_REGISTRATION` subtypes, and the
  ROAS/value goals. v1 is the `APP_CAMPAIGN` subtype with install and in-app-action goals.
- Removing campaigns, creating Google Ads accounts, billing, conversion-action setup,
  Firebase/Play linking (done once by the user in Google's consoles).
- Generating images or video. The server attaches assets the user already has.
- A scheduler. The server ships the daily-review prompt; running it on a schedule is the
  client's job (README shows one example).
- Contributing the tools upstream. Possible later, once the add-on has proven itself.

## Anti-goals (would make it a failure even if it works)

- Any code path that changes a budget amount after creation, or creates a campaign with a
  budget the user did not type.
- A target-cost change outside the user's limits, or one that goes through when the limits
  cannot be read.
- Strategy rules with invented numbers. Every threshold in the playbook is traced to a Google
  source or marked as a default the user can change.
- Storing or transmitting users' credentials, or anything that needs us to run a server.
- Internal data (account IDs, app specifics, workspace paths) in the public repo.

## Constraints (verified 2026-11-20)

- **API access.** Developer tokens were retired on 2026-09-09. Access level now belongs to the
  Google Cloud project: enabling the Google Ads API gives Test access (test accounts only);
  Explorer access is applied for in the Cloud Console, is approved automatically in most cases,
  and allows 2,880 operations/day on production accounts. That is enough for daily management
  of a few campaigns. Explorer blocks billing, planning, account creation and user management,
  none of which we use. Source: developers.google.com/google-ads/api/docs/api-policy/access-levels
- **App campaign shape** (developers.google.com/google-ads/api/docs/app-campaigns/*):
  `advertising_channel_type = MULTI_CHANNEL`, sub-type `APP_CAMPAIGN`; dedicated non-shared
  daily `CampaignBudget` (`explicitly_shared = false`); no portfolio bidding; target lives on the
  campaign (`target_cpa.target_cpa_micros` = target cost per install or per action); ad-group
  bid overrides are rejected; `contains_eu_political_advertising` must be declared;
  conversion goals go in `selective_optimization.conversion_actions`. Everything can be created
  atomically in one `GoogleAdsService.Mutate` with negative temporary IDs.
- **The ad.** One `AdGroupAd` per ad group, an `AppAdInfo` with 2–5 headlines, 1–5
  descriptions, up to 20 images, 20 YouTube videos, 20 HTML5 bundles. It cannot be paused or
  removed; creatives are changed in place with `AdService.MutateAds`; serving is stopped by
  pausing the ad group or campaign.
- **Reporting** is plain GAQL (`campaign`, `ad_group_ad_asset_view`, `app_top_combination_view`,
  `change_event`), which the official `search` tool already runs.
- **Upstream is young and moving** (v0.0.x, commits every few days, API v25 imports). We depend
  on its internals (`ads_mcp.coordinator.mcp`, `ads_mcp.propose_and_confirm`, `ads_mcp.utils`).
- **The 0.0.6 release is behind upstream's main.** The audit log (`ads_mcp.audit`) exists only
  on main. Until a release ships it, the record of what changed is Google's own change history
  (`change_event`). Checked against the installed package on 2026-11-20.
- **Mounting works** (spike, 2026-11-20): a sub-server mounted on `ads_mcp.coordinator.mcp` with
  `namespace="app_campaigns"` lists as `app_campaigns_<tool>` beside upstream's seven tools.
  Upstream's `tools_config.yaml` loader treats unknown namespaces as disabled, so ours is
  mounted directly and is always on.
- Test accounts never serve ads, so they validate mutations but produce no metrics.
- Upstream v0.0.6 has no generic mutate tool: its four mutating tools only reach assets,
  YouTube uploads and labels. Nothing upstream can touch a `CampaignBudget`.

## Approaches

| | What | Trade-off |
|---|---|---|
| **A. Add-on package (chosen)** | Our package depends on `google-ads-mcp` from PyPI, imports its server object and mounts our tools next to the official ones. | Least code; reuses safety flow and auth. Depends on upstream internals, so the version is pinned exactly and bumped on purpose. |
| B. Fork | Copy upstream and add our tools inside. | Same result for users; every upstream release must be merged by hand, forever. |
| C. Upstream PRs | Offer the tools to Google. | Best reach, no maintenance; needs their CLA and review, and they have deliberately shipped no campaign or bid tools so far. Not ours to schedule. |

Dropped earlier: writing a server from scratch (re-does reads, auth and safety that exist), and
building on the community read-write servers (unaudited, Search-focused, no App campaigns).

The owner picked **A** on 2026-11-20.

## Decided by the owner

- Community, open-source MCP; runs locally; no rented servers.
- Claude manages the whole campaign except the daily budget.
- Target cost (per install / per action): Claude may change it on its own **inside a range the
  user sets** (his example: $0.80–$1.50).
- Approach A.

## Design (my calls unless marked; correct any of them)

### Package

- Python ≥ 3.11, Apache-2.0, with a NOTICE crediting upstream and a "not affiliated with Google"
  line. Working name `google-ads-app-campaigns-mcp` (module `ads_app_mcp`).
- Dependency: `google-ads-mcp==0.0.6` (exact pin).
- Entry point: import `ads_mcp.coordinator.mcp` (which already mounts the official tools),
  mount our sub-server on it with `namespace="app_campaigns"`, then hand over to upstream's
  `run_server()`, which registers its resources and picks the transport. stdio + Application Default
  Credentials is the documented path; upstream's HTTP/OAuth mode keeps working untouched.
- All our mutating tools use upstream's `propose_and_confirm` (default `validate_only=True`
  returns readable change items + checksum; commit needs the same params + checksum).

### Tools (namespace `app_campaigns`)

| Tool | Does | Notes |
|---|---|---|
| `create_app_campaign` | Budget + campaign + location/language criteria + ad group + app ad, atomically. | Always created `PAUSED`. Three goals: installs with a target cost, installs without a target, in-app actions with a target cost. `daily_budget` and `contains_eu_political_advertising` are required with no default. Target cost must be inside the guardrail range if one is configured. |
| `set_campaign_status` | `ENABLED` / `PAUSED`. | No remove. |
| `set_target_cost` | Changes `target_cpa_micros`. | Guardrailed, see below. |
| `update_app_ad` | Replaces headlines / descriptions / images / videos on the campaign's app ad. | Validates the 2–5 / 1–5 / ≤20 limits before calling the API. Images and videos are created with upstream's asset tools and passed by resource name. |
| `set_campaign_targeting` | Adds/removes location and language criteria. | |

No read tools: statistics come from upstream's `search`. No budget tool exists at all.

### The money boundary

- **Budget.** Typed by the user at creation; shown in the proposal; never touched again by any
  tool. A test walks every tool with a mocked client and fails if any builds a
  `CampaignBudgetOperation` other than the single create inside `create_app_campaign`.
- **Target cost guardrails.** A YAML file owned by the user, outside the repo, path in
  `GOOGLE_ADS_APP_MCP_GUARDRAILS`. Values are in the account's currency (not micros):
  `min`, `max`, `max_step_pct`, `min_days_between_changes`. Per customer there are two
  defaults, one for cost-per-install goals and one for cost-per-action goals, plus optional
  per-campaign overrides by campaign ID. The defaults are what cover a campaign that does not
  exist yet.
  - `create_app_campaign` with a target-based goal refuses unless a range for that goal kind
    exists for the customer and the initial target is inside it. So a campaign can never be
    created in a state the daily review cannot manage.
  - `set_target_cost` works on any `APP_CAMPAIGN` that already has a `target_cpa`, whether we
    created it or the user did in the UI, as long as a range covers it. A campaign with no
    target is refused.
  - It refuses when the file is missing, no range applies, the value is out of range, or the
    step is too large.
  - Cooldown: any change to the target in the last `min_days_between_changes` days counts,
    whoever made it (read from `change_event`, which keeps about 30 days; the setting is
    capped at 28). If that history cannot be read, it refuses. It fails closed.
- **Goal type.** Switching to a "without target" goal removes the cost cap, so it is not
  exposed in v1; the goal is chosen at creation.
- **Unattended mode.** With `GOOGLE_ADS_APP_MCP_UNATTENDED=1` (set by the scheduled run), only
  `set_target_cost` and `update_app_ad` may commit. Every other tool still returns proposals
  but rejects `validate_only=False`. This is what makes "pausing or opening new countries waits
  for the user" a guarantee instead of an instruction.
- **What this protects against.** Model mistakes and drift, not a hostile process: an agent
  with a shell on the same machine also holds the user's Google credentials and could call the
  API directly, so no local file or flag is a security boundary. The README says so plainly
  and names the real backstops: the daily budget itself, which only the user sets, and, for
  stronger isolation, upstream's container mode with the guardrails file mounted read-only.
  The smoke test's golden tool list makes any new upstream tool show up for review when the
  pin is bumped.

### Strategy: playbook + daily review

- `resource://app-campaign-playbook`: the GAQL recipes (spend, installs, cost per install vs
  target, budget use, asset performance labels, top combinations, recent changes) and the
  decision rules (when a campaign is still learning and must be left alone, when an asset has
  enough data to be judged, how far and how often to move the target, when the budget is the
  limit). Each rule cites its Google source or is flagged as an adjustable default.
- Prompt `daily_review`: run the recipes, apply what the playbook and guardrails allow, and
  write a short report — yesterday / 7 days / 30 days, what was changed and why, proposals
  waiting for the user, and a **budget recommendation as text only** when budget is the limit.
- No local state: what was changed and when is read back from Google's own `change_event`.

### Testing

- Unit tests with a mocked `GoogleAdsClient`, following upstream's `tests/tools` patterns: one
  per tool for the operations it builds, plus guardrail edges (min, max, step, cooldown,
  missing file, missing entry, unreadable history) and unattended-mode rejections.
- The budget-invariant test above.
- A smoke test asserting the mounted tool list equals upstream's golden list plus ours, so an
  upstream bump that breaks mounting fails CI.
- A documented manual run against a Google Ads **test account** (Test access, no approval):
  propose, commit, then update, retarget, change target, pause, using a public app's package
  name the way Google's `AddAppCampaign` sample does. Not in CI (needs credentials).
- **v1 is done** when the unit tests pass and that run has been executed. Whatever a test
  account refuses to exercise is listed in the README as unverified until the first run on a
  real account, which is the follow-up work below.

### Repo and release

- Its own git repository with a GitHub remote from the first commit, on the owner's personal
  account, created **private**.
- Going public and publishing to PyPI are separate steps that wait for the owner's word.
- README written with the `readme-craft` skill: what it adds over the official server, the
  one-time Google setup (Cloud project, enable API, Explorer access, OAuth client, ADC login,
  Play/Firebase link so install conversions exist), the guardrails file, the daily review, and
  upstream's telemetry header notice.

### Build order

1. Skeleton, mount, smoke test. 2. `create_app_campaign`. 3. Status, targeting, ad update.
4. Guardrails, `set_target_cost`, unattended mode. 5. Playbook and `daily_review`.
6. README and the test-account run.

Using it on the owner's real account (his app, his first campaign, the schedule on his machine) is
a separate piece of work after v1, and needs his one-time Google setup first.

## Left to the builder

- Exact parameter names and types; how targeting is expressed (geo target constant IDs vs names).
- The playbook's actual thresholds, after reading Google's App campaign guidance; and field
  names in the recipes, checked with upstream's `metadata_get_resource_metadata`.
- Guardrails file schema details and error wording.
- CI setup (lint, tests) mirroring upstream's `nox` sessions where it is cheap to do so.
