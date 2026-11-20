# App campaign playbook

This playbook tells an AI agent how to read a Google Ads App campaign and what
it may change. It is served by this MCP server as
`resource://app-campaign-playbook` and run by the `daily_review` prompt. The
aim is a campaign that gets a little better every day while the person who owns
it keeps control of money.

Three limits are enforced by the server, not by this text:

- No tool changes a budget. There is no budget tool at all.
- `app_campaigns_set_target_cost` only moves the target inside the range, the
  step size and the waiting time the user wrote in their guardrails file. It
  refuses anything else, and it refuses when the file or the change history
  cannot be read.
- In an unattended run only `app_campaigns_set_target_cost` and
  `app_campaigns_update_app_ad` can commit. Every other `app_campaigns_`
  tool can only propose.

Every number in "Decision rules" links the Google page it comes from, or is
marked as an adjustable default with the reason for it. Google's pages change:
if a linked page now says something different, follow the page and tell the
user.

## How to read an App campaign

Run each recipe with the `search_search` tool. It takes the query in parts,
not as one string:

- `customer_id`: the customer being reviewed. The query text never contains it.
- `fields`: the names after SELECT. `resource`: the name after FROM.
- `conditions`: each condition between the ANDs, as its own item.
- `orderings` and `limit`: from ORDER BY and LIMIT, when the recipe has them.

Replace `{campaign_id}` with the campaign's numeric ID and `{date_range}` with
the range the recipe names. `{today}` is today's date and `{start_date}` the
date 14 days before it, both as YYYY-MM-DD in the account's time zone
(`customer.time_zone`, recipe 2). Fields ending in `_micros`, and
`metrics.cost_per_conversion`, are in millionths of the account currency:
divide by 1,000,000 before comparing or reporting. `LAST_7_DAYS`,
`LAST_14_DAYS` and `LAST_30_DAYS` end yesterday and never include today
([date ranges](https://developers.google.com/google-ads/api/docs/query/date-ranges)).

Installs are counted as conversions in App campaigns
([App campaign reporting](https://developers.google.com/google-ads/api/docs/app-campaigns/reporting)). On a campaign row, `metrics.conversions`
is what the campaign optimises for: installs on an install goal, the chosen
in-app actions on an action goal. Recipe 4 shows the split by action.

### 1. Find the App campaigns

Only when no campaign was named.

```sql
SELECT campaign.id, campaign.name,
  campaign.app_campaign_setting.bidding_strategy_goal_type
FROM campaign
WHERE campaign.advertising_channel_type = 'MULTI_CHANNEL'
  AND campaign.advertising_channel_sub_type = 'APP_CAMPAIGN'
  AND campaign.status = 'ENABLED'
```

### 2. Campaign state

```sql
SELECT campaign.id, campaign.name, campaign.status,
  campaign.advertising_channel_sub_type, campaign.primary_status,
  campaign.primary_status_reasons, campaign.bidding_strategy_system_status,
  campaign.app_campaign_setting.bidding_strategy_goal_type,
  campaign.app_campaign_setting.app_id,
  campaign.target_cpa.target_cpa_micros, campaign.start_date_time,
  campaign_budget.amount_micros, customer.currency_code,
  customer.time_zone
FROM campaign
WHERE campaign.id = {campaign_id}
```

`campaign_budget.amount_micros` is the daily budget.
`campaign.target_cpa.target_cpa_micros` is the target cost per install or per
in-app action; it is empty on an "installs without a target" goal.
`primary_status_reasons` and `bidding_strategy_system_status` are Google's
own reasons for a campaign that is learning or held back
([status reasons](https://developers.google.com/google-ads/api/reference/rpc/v25/CampaignPrimaryStatusReasonEnum.CampaignPrimaryStatusReason), [bidding status](https://developers.google.com/google-ads/api/reference/rpc/v25/BiddingStrategySystemStatusEnum.BiddingStrategySystemStatus)).

### 3. Spend, budget use, conversions and cost per conversion

Run it four times, with `{date_range}` set to `YESTERDAY`, `LAST_7_DAYS`,
`LAST_14_DAYS` and `LAST_30_DAYS`.

```sql
SELECT campaign.id, campaign_budget.amount_micros, metrics.cost_micros,
  metrics.conversions, metrics.cost_per_conversion, metrics.impressions,
  metrics.clicks
FROM campaign
WHERE campaign.id = {campaign_id}
  AND segments.date DURING {date_range}
```

Average daily spend is the cost divided by the days in the range. Budget use is
the average daily spend divided by the daily budget
(`campaign_budget.amount_micros`). That is today's budget, so if the user
changed it inside the range, call the share approximate. No row means nothing
was spent.

### 4. Conversions by action

```sql
SELECT campaign.id, segments.conversion_action,
  segments.conversion_action_name, metrics.conversions,
  metrics.all_conversions
FROM campaign
WHERE campaign.id = {campaign_id}
  AND segments.date DURING LAST_30_DAYS
```

### 5. The ad as it is now

```sql
SELECT ad_group.id, ad_group.name, ad_group_ad.ad.id,
  ad_group_ad.ad.app_ad.headlines, ad_group_ad.ad.app_ad.descriptions,
  ad_group_ad.ad.app_ad.images, ad_group_ad.ad.app_ad.youtube_videos
FROM ad_group_ad
WHERE campaign.id = {campaign_id}
  AND ad_group_ad.status != 'REMOVED'
  AND ad_group.status != 'REMOVED'
```

`app_campaigns_update_app_ad` replaces a whole list, so copy every text you
keep from here exactly, in the same order. When the campaign has several ad
groups, pass `ad_group_id`.

### 6. How each asset performs

```sql
SELECT ad_group.id, ad_group_ad_asset_view.asset,
  ad_group_ad_asset_view.field_type,
  ad_group_ad_asset_view.performance_label,
  ad_group_ad_asset_view.enabled,
  ad_group_ad_asset_view.policy_summary,
  asset.type, asset.name, asset.text_asset.text,
  metrics.impressions, metrics.clicks, metrics.conversions,
  metrics.cost_micros
FROM ad_group_ad_asset_view
WHERE campaign.id = {campaign_id}
  AND segments.date DURING LAST_14_DAYS
```

Ignore rows whose `enabled` is false. `policy_summary` holds the asset's
`approval_status` (APPROVED, DISAPPROVED and others); the API does not accept
that inner field on its own in a query. `field_type` is the slot (HEADLINE,
DESCRIPTION, MARKETING_IMAGE, YOUTUBE_VIDEO and others). `performance_label`
is Google's rating: PENDING, LEARNING, LOW, GOOD or BEST
([asset reporting](https://support.google.com/google-ads/answer/6310436)).
An asset that is in recipe 5 but not here served nothing in the range.

### 7. Top asset combinations

```sql
SELECT ad_group.id, app_top_combination_view.ad_group_top_combinations
FROM app_top_combination_view
WHERE campaign.id = {campaign_id}
```

The combinations Google served most, each as asset resource names with the
slot they served in. Use it to see which texts work well together when you
write a new one; it does not change any rule.

### 8. Change history

```sql
SELECT change_event.change_date_time, change_event.change_resource_type,
  change_event.resource_change_operation, change_event.changed_fields,
  change_event.client_type, change_event.old_resource,
  change_event.new_resource
FROM change_event
WHERE campaign.id = {campaign_id}
  AND change_event.change_date_time >= '{start_date}'
  AND change_event.change_date_time <= '{today} 23:59:59'
ORDER BY change_event.change_date_time DESC
LIMIT 1000
```

The dates are written out because `LAST_14_DAYS` would leave out today, and
today's changes are the ones that matter most here. A `change_event` query
needs a date range inside the last 30 days and a LIMIT
([change history](https://developers.google.com/google-ads/api/docs/change-event)). `changed_fields` comes back from `search_search` as one comma-separated string
with the field names in camelCase. A CAMPAIGN row with operation UPDATE whose
`changed_fields` include `targetCpa.targetCpaMicros` (the API field
`target_cpa.target_cpa_micros`) is a target change, with the old and new value
in `old_resource` and `new_resource`; one whose `changed_fields` include
`status` is a pause or an enable. A CAMPAIGN row with operation CREATE is the
campaign's creation, which also sets the target. An AD row is a change of the ad's texts,
images or videos. Times are in the account's time zone. A change does not appear at once: Google
documents up to three minutes ([change history](https://developers.google.com/google-ads/api/docs/change-event)), and this project's
first test-account run saw more than six, so a change made minutes ago may
not be listed yet. `client_type` tells the user's own edits
(`GOOGLE_ADS_WEB_CLIENT`) from changes made through the API
(`GOOGLE_ADS_API`).

## Decision rules

Go through these in order for each campaign. Where a rule says to change
nothing, skip the rules that would change something.

### Is there enough to judge?

- Not an active App campaign: if `campaign.status` is not ENABLED or
  `campaign.advertising_channel_sub_type` is not APP_CAMPAIGN, report only.
- Spend but no conversions: if the last 7 days had spend and zero conversions,
  change nothing on the campaign, put it first in the Summary and ask the user
  to check that conversions are still being tracked (for example the link
  between the app and Google Ads). Adjustable default: a full week of spend
  without one conversion is more likely a tracking problem than a campaign
  problem, and changing the campaign would not fix it.
- Learning: if `campaign.primary_status` is LEARNING,
  `campaign.bidding_strategy_system_status` starts with LEARNING_, or
  `campaign.primary_status_reasons` contains BIDDING_STRATEGY_LEARNING, change
  nothing on this campaign today and say it is still learning. Google advises
  keeping top-performing ad assets active without edits during the learning
  period of the first 100 conversions ([goal setup](https://support.google.com/google-ads/answer/6167156)), and shows the
  Learning status for up to 7 days ([Smart Bidding learning](https://support.google.com/google-ads/answer/13020501)). Changing
  nothing at all while a learning signal shows is this playbook's adjustable
  default: a change made while Google is still adjusting cannot be judged, and
  the status clears within a week.
- New campaign: if `campaign.start_date_time` is less than 14 days ago, or the
  change history shows the campaign was enabled in the last 14 days, change
  nothing. Google asks for 1-2 conversion cycles or 14 days before judging
  performance ([goal setup](https://support.google.com/google-ads/answer/6167156)).
- Too few conversions for a target change: with fewer than 100 conversions in
  `LAST_14_DAYS`, do not change the target (the ad text rules below still
  apply). Google says changes before 100 conversions can disrupt learning
  ([About App campaigns](https://support.google.com/google-ads/answer/6247380), [best practices](https://support.google.com/google-ads/answer/14104492)) and to wait for at
  least 100 conversions before the next bid change
  ([new directions](https://support.google.com/google-ads/answer/7678575)). The 14-day counting window is an adjustable
  default: because the target-cost rules below also require no target change
  in those 14 days, every one of these conversions came after the last change.
- Google's pages differ here: the general Smart Bidding page says target and
  creative changes never trigger the Learning status
  ([Smart Bidding learning](https://support.google.com/google-ads/answer/13020501)), while the App campaign pages above advise
  waiting for 100 conversions. This playbook follows the App campaign pages,
  which are stricter.
- Rare in-app action: on an in-app action goal, if the last 7 days averaged
  fewer than 10 conversions a day, say so and propose choosing a more common
  action. Google wants the chosen action completed by at least 10 different
  users a day ([setup guide](https://support.google.com/google-ads/answer/6167162), [goal setup](https://support.google.com/google-ads/answer/6167156)), and fewer
  conversions means fewer users. The 7-day window is an adjustable default: a
  full week evens out weekday swings and is still short enough to catch an
  action that stopped being completed. No tool changes this; the user does.
  This rule does not stop the others.

### Budget (never changed by the agent)

- The budget is the limit when any of these holds:
  - `primary_status_reasons` contains BUDGET_CONSTRAINED, or
    `bidding_strategy_system_status` is LIMITED_BY_BUDGET
    ([status reasons](https://developers.google.com/google-ads/api/reference/rpc/v25/CampaignPrimaryStatusReasonEnum.CampaignPrimaryStatusReason), [bidding status](https://developers.google.com/google-ads/api/reference/rpc/v25/BiddingStrategySystemStatusEnum.BiddingStrategySystemStatus),
    [limited by budget](https://support.google.com/google-ads/answer/6385220));
  - the campaign has a target and the daily budget is below 50 times the target
    cost per install, or below 10 times the target cost per in-app action
    ([best practices](https://support.google.com/google-ads/answer/14104492)). [Goal setup](https://support.google.com/google-ads/answer/6167156) says 10-15 times for in-app
    actions; this playbook uses the lower figure so that it does not raise the
    alarm too early;
  - the last 7 days spent at least 95 % of 7 times the daily budget. Adjustable
    default: nearly all of the budget was used every day. Google can spend up
    to twice the daily budget on a single day and balances it over the month
    ([average daily budgets](https://support.google.com/google-ads/answer/6385083)), so the week's average is the fair
    comparison.
- When it holds, write the Budget note with the numbers that show it. Never
  call a tool for it and never work around it: the user decides.

### Target cost

These rules apply only when the goal is OPTIMIZE_INSTALLS_TARGET_INSTALL_COST
or OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST and the campaign has a
target. Any other campaign gets no target change.
OPTIMIZE_IN_APP_CONVERSIONS_TARGET_INSTALL_COST (installs from users likely to
take an in-app action) also has a target, but gets no automatic target change
in this version: this server's create tool does not offer that goal, the
guardrails file has no range for it, and `app_campaigns_set_target_cost`
refuses it. Report its numbers only.

- Wait: no target change by anyone in the last 14 days (change history). That
  is the window Google uses to judge results ([goal setup](https://support.google.com/google-ads/answer/6167156)). The user's
  `min_days_between_changes` also applies and the tool enforces it; the longer
  wait wins.
- Size: at most 20 % of the current target per change. Google advises against
  target changes above 20 % ([tips](https://support.google.com/google-ads/answer/9176652)) and against raising a target by
  more than 20 % per day ([new directions](https://support.google.com/google-ads/answer/7678575)). The user's `max_step_pct`
  also applies and the tool enforces it; the smaller step wins. Round the new
  value toward the current target, to the smallest unit of the currency.
- Budget first: when the budget is the limit, do not raise the target. A higher
  target would then only make each install more expensive.
- Raise one step when the target holds the campaign back, in one of two ways.
  Either `primary_status_reasons` contains BIDDING_STRATEGY_CONSTRAINED
  ("could capture more conversion value by adjusting CPA/ROAS targets",
  [status reasons](https://developers.google.com/google-ads/api/reference/rpc/v25/CampaignPrimaryStatusReasonEnum.CampaignPrimaryStatusReason)) and the 7-day cost per conversion is at or below the
  target; or the last 7 days spent less than 80 % of 7 times the daily budget
  while the 7-day cost per conversion was at least 90 % of the target and not
  above it. Google calls a campaign bid-constrained when "the achieved CPI or
  CPA is closely tracking or intermittently exceeding the set tCPI or tCPA",
  and advises raising the bid ([best practices](https://support.google.com/google-ads/answer/14104492)). The 80 %, the 90 % and
  the 7-day window are adjustable defaults: a fifth of the budget left unspent
  for a week means something other than the budget holds spending back; a
  cost at 90 % of the target or more is this playbook's reading of "closely
  tracking", so a campaign well under its target that still underspends (too
  little inventory or too small an audience) gets no raise; and a full week
  evens out weekday swings. Never go above the user's `max`.
- Cost above target: if both the 14-day and the 30-day cost per conversion are
  more than 20 % above the target, do not change the target on your own.
  Propose lowering it one step and explain the trade-off: cheaper installs, but
  fewer of them. The 20 % here is an adjustable default: a smaller gap is
  ordinary day-to-day variation, and lowering the target cuts volume, which is
  the user's call. The 14-day and 30-day windows are adjustable defaults too:
  requiring both keeps one bad fortnight from starting a proposal. Google advises single adjustments rather than raising and
  lowering bids again and again ([best practices](https://support.google.com/google-ads/answer/14104492)).
- Otherwise leave the target alone. Google advises against cycling bids up and
  down ([About App campaigns](https://support.google.com/google-ads/answer/6247380)).
- At the user's limit: if the next step would cross the user's `min` or
  `max`, the tool refuses. Report that the target is at the user's limit.
  Never edit the guardrails file or the environment to get past it.

### Ad texts

- Ratings that count: only LOW, GOOD and BEST. Any other value is not a rating
  to act on; PENDING and LEARNING mean Google has not rated the asset yet
  ([asset reporting](https://support.google.com/google-ads/answer/6310436)). Ratings only appear once an asset has left
  Learning ([creative assets](https://support.google.com/google-ads/answer/6167158)), so a LOW rating already means Google had
  enough data.
- Stable window: Google rates assets on the last 14 days
  ([asset reporting](https://support.google.com/google-ads/answer/6310436)). Judge texts only when the change history shows
  no AD change in those 14 days, so that every rating describes the ad as it is
  now.
- One kind of change per run: if the target changed in this run, leave the ad
  texts for a later run. Adjustable default: the next review can then tell
  which change moved the numbers.
- Room to add: the API allows 2-5 headlines and 1-5 descriptions
  ([App ad](https://developers.google.com/google-ads/api/docs/app-campaigns/create-ad-group)), and the tool enforces it. Google's Help pages disagree on
  text counts ("4 for text" in [creative assets](https://support.google.com/google-ads/answer/6167158), "up to 10 text" in
  [setup guide](https://support.google.com/google-ads/answer/6167162)); the API limit is the one that applies here. When a
  field has fewer than 5 texts and one of them is rated BEST, add one new text
  modelled on it and keep all the others. Google advises adding assets like the
  BEST ones until the maximum is reached ([creative assets](https://support.google.com/google-ads/answer/6167158)) and
  replacing LOW ones only after that ([new directions](https://support.google.com/google-ads/answer/7678575)).
- Full field: when a field has 5 texts, replace exactly one LOW text of that
  field. Never replace a GOOD or BEST text. At the maximum Google advises
  replacing LOW assets, gradually ([creative assets](https://support.google.com/google-ads/answer/6167158)).
- One text per run: change at most one text per campaign per run. Adjustable
  default for Google's "gradually" ([creative assets](https://support.google.com/google-ads/answer/6167158)); with the 14-day
  window above it means at most one text change per ad every two weeks.
- A new headline has at most 30 characters and a new description at most 90
  ([assets in App campaigns](https://support.google.com/google-ads/answer/6357595)). Use at most one exclamation mark
  ([tips](https://support.google.com/google-ads/answer/9176652)).
- A new text is written in the language of the ad's current texts and built
  only from what the user already says: texts the asset recipe shows as
  APPROVED, and the app's store listing if you can read it. Add no price,
  discount, "free", guarantee, ranking or superlative (best, number one,
  fastest), no name of another company or brand, and no claim about the app,
  unless the user's own approved texts already say it. It must make sense on
  its own and must not repeat or nearly repeat a text already in the ad.
- If you cannot write a text that meets every rule above, change nothing and
  say why in the report.
- Disapproved texts (`approval_status` DISAPPROVED): report them and propose a
  replacement; never commit it alone.
- Images and videos: never change them in a run nobody is watching, unless the
  request that started the run supplied the replacement asset for this
  campaign. Otherwise propose the change.

## What the agent may do alone and what waits for the user

The server enforces part of this: in an unattended run only
`app_campaigns_set_target_cost` and `app_campaigns_update_app_ad` can commit.
It cannot tell an ad text from an image, and it does not gate Google's own
asset and label tools; those parts are this playbook's rules.

| Action | Run with nobody watching | User in the conversation |
|---|---|---|
| `app_campaigns_set_target_cost`, as "Target cost" allows | Commit | Propose, commit after a yes |
| `app_campaigns_update_app_ad`, texts only, as "Ad texts" allows | Commit | Propose, commit after a yes |
| `app_campaigns_update_app_ad`, images or videos | Propose (commit only with a replacement the user supplied) | Propose, commit after a yes |
| Anything "Decision rules" says to propose, such as lowering the target | Propose | Propose, commit after a yes |
| `app_campaigns_set_campaign_status` | Propose | Propose, commit after a yes |
| `app_campaigns_set_campaign_targeting` | Propose | Propose, commit after a yes |
| `app_campaigns_create_app_campaign` | Propose | Propose, commit after a yes |
| `assets_create_asset`, `assets_upload_video_asset`, label tools | Do not call | Only when the user asks |
| The budget | Never; Budget note only | Never; Budget note only |
| The guardrails file and the environment | Never | Never |

To commit, call the tool with `validate_only=True`, check that its
`change_items` say exactly what you meant, then call it again with the same
arguments, `validate_only=False` and the `checksum` it returned. If the commit
is refused because something changed in between, start again from fresh
numbers; never reuse an old checksum. A proposal is the same first call alone:
put its `change_items` in the report.

Refusals are final. When a tool refuses (outside the user's range, a step too
big, too soon after the last change, a commit not allowed in an unattended run,
limits or change history that cannot be read), do not retry with another value,
a smaller step, another tool or another campaign to get the same effect.
Report the refusal in plain words.

## Report format

Write in the user's language and in plain words, for someone who is not a
marketer: "cost per install", not "CPI". Give money in the account currency
with its code, never in micros. Lead with what happened and what was changed;
the numbers come after. Write "not available" for any number you could not
read; never estimate one. With several campaigns, give sections 2 to 5 once per
campaign, under its name.

1. **Summary**: three lines. How the campaign did, what you changed, what needs
   the user.
2. **Numbers**: one table.

   | | Yesterday | Last 7 days | Last 30 days |
   |---|---|---|---|
   | Spend | | | |
   | Installs (or in-app actions) | | | |
   | Cost per install, and the target | | | |
   | Share of the daily budget used | | | |

3. **Changes applied today and why**: each change with before and after and the
   rule behind it, in one sentence. Every new ad text in full, with the text it
   replaced. If nothing changed, say so and why.
4. **Proposals waiting for you**: what each would do, why, and that it waits
   for a yes. Tool refusals go here too, in plain words.
5. **Budget note**: only when the budget is the limit. The numbers that show
   it, what Google's guidance would suggest, and that only the user changes the
   budget.
