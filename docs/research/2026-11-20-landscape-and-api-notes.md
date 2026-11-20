# Research notes — landscape and API facts

Collected 2026-11-20. Everything here was read from the source named next to it on that date.

## Existing Google Ads MCP servers

| Server | Who | Writes? | Verdict |
|---|---|---|---|
| `googleads/google-ads-mcp` (v0.0.6, Apache-2.0, ~1.2k stars) | Google | Assets, YouTube uploads, labels only | Our base. No campaign, status, bid, ad or targeting tools. |
| `google-ads-mcp-rw` (PyPI, MIT) | community | Search campaigns, ad groups, keywords, RSAs | Unaudited; no App campaigns found. |
| `bluealpha-google-ads-mcp` (PyPI) | community | 16 write tools for Search-style CRUD | Same. |
| `googleadsagent-mcp` (PyPI, MIT) | community | `create_campaign`, `generic_mutate` | Same; generic mutate has no money boundary. |
| Ryze AI hosted server | vendor | Approval-gated changes | Hosted: the user's account access goes to a third party. |

None of them creates or manages an App campaign.

## API access (developers.google.com/google-ads/api/docs/api-policy/access-levels)

- Developer tokens were retired on 2026-09-09. The access level belongs to the Google Cloud
  project used for the OAuth credentials.
- Test: granted on enabling the API; test accounts only; 15,000 ops/day.
- Explorer: applied for on the Google Ads API Overview page in Cloud Console; automated review
  in most cases; 2,880 ops/day on production accounts. Blocks account creation/linking, user
  management, planning services, billing, multi-party approvals.
- Basic: needs brand verification of the Cloud project; 15,000 ops/day.
- Standard: manual audit, Required Minimum Functionality applies; unlimited.
- A Test-level project calling a production account gets
  `AuthorizationError.CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` (v25).

## App campaigns (developers.google.com/google-ads/api/docs/app-campaigns/*)

- `advertising_channel_type = MULTI_CHANNEL`; sub-types `APP_CAMPAIGN`,
  `APP_CAMPAIGN_FOR_ENGAGEMENT`, `APP_CAMPAIGN_FOR_PRE_REGISTRATION` (all immutable).
- Budget: daily `CampaignBudget` with `explicitly_shared = false` (the default `true` fails).
  No shared budgets, no portfolio bidding strategies.
- `app_campaign_setting`: `app_store` and `app_id` immutable; `bidding_strategy_goal_type` mutable.
- Goal → fields, for the `APP_CAMPAIGN` goals v1 exposes:

  | Goal | `bidding_strategy_goal_type` | Bidding field | Conversion actions |
  |---|---|---|---|
  | Installs, target cost | `OPTIMIZE_INSTALLS_TARGET_INSTALL_COST` | `target_cpa.target_cpa_micros` | optional, ≤ 1 DOWNLOAD |
  | Installs, no target | `OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST` | empty `maximize_conversions` | optional, ≤ 1 DOWNLOAD |
  | In-app actions, target cost | `OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST` | `target_cpa.target_cpa_micros` | required post-install actions |

- `maximize_conversions.target_cpa_micros` is rejected on `MULTI_CHANNEL`.
- `contains_eu_political_advertising` must be declared on the campaign.
- Conversion goals go in `selective_optimization.conversion_actions`.
- Location/language are `CampaignCriterion` rows. Negative keywords only via shared sets.
- Everything can be created in one `GoogleAdsService.Mutate` with negative temporary IDs.
- Ad group: leave `type` unset; on `APP_CAMPAIGN` the API rejects ad-group `target_cpa_micros`.
- Ad: one `AdGroupAd` per ad group; `AppAdInfo` with 2–5 `headlines`, 1–5 `descriptions`,
  ≤ 20 `images`, ≤ 20 `youtube_videos`, ≤ 20 `html5_media_bundles`. Text can be inline
  `AdTextAsset`; other assets are referenced by resource name. The `AdGroupAd` cannot be paused
  or removed; change creatives with `AdService.MutateAds`; stop serving by pausing the ad group.
- Reporting resources: `campaign` (segment by `segments.date`, `segments.conversion_action`),
  `ad_group_ad_asset_view` (per-asset metrics and performance label),
  `app_top_combination_view`, `change_event`.
- Test accounts create campaigns but never serve, so they return no metrics.
  `validate_only` checks a request without executing it.

Raw page text from that day is in `tmp/google-docs/` (not tracked).

## Observed on the real API (test account, 2026-11-20)

- A Test-level Cloud project reaches test accounts with no developer token and no Explorer
  access. `customer.test_account` is true and `customer.status` is CLOSED for both the test
  manager and the test client.
- The atomic create with negative temporary IDs passes `validate_only` and commits. An empty
  `maximize_conversions` with `OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST` passes validation.
  Two headlines and one description are accepted.
- `change_event`: a target change is a CAMPAIGN / UPDATE row whose `changed_fields` path is
  `target_cpa.target_cpa_micros`, with old and new values in `old_resource` / `new_resource`;
  the campaign's creation is a CAMPAIGN / CREATE row that lists the target among its fields.
  `campaign.id` and `change_event.campaign` both work as filters. Events appeared between six
  and sixteen minutes after the change, not within the documented three.
- `DURING LAST_14_DAYS` ends yesterday; `DURING TODAY` works on `change_event`.
- `ad_group_ad_asset_view.policy_summary` is selectable; `...policy_summary.approval_status`
  is rejected as an unrecognised field. Assets of a campaign that never served are still listed,
  with performance label PENDING.
- Through upstream's `search_search`, a field mask is returned as one comma-separated string
  of camelCase paths (`targetCpa.targetCpaMicros`), and enums as their names.
- One `search_search` call takes roughly ten to fifteen seconds end to end.

## Upstream internals we rely on (installed 0.0.6, read from site-packages)

- `ads_mcp.coordinator.mcp`: the singleton `FastMCP`; importing the module mounts upstream's
  five namespaces. Importing needs no credentials.
- `ads_mcp.server.run_server()`: imports the four resources, picks stdio or streamable-http.
- `ads_mcp.propose_and_confirm.propose_and_confirm(*, customer_id, validate_only, checksum,
  operation_payload, change_items, validate_fn=None, commit_fn, error_prefix)` → `str`;
  `build_change_items(operation, entity_readable, primary_key_readable, fields,
  original_fields=None)`. The checksum binds `customer_id` + `operation_payload` only.
- `ads_mcp.utils`: `get_googleads_client(login_customer_id=None)`,
  `get_googleads_service_from_client(client, name)`, `clean_customer_id`, `extract_id`,
  `format_google_ads_error`.
- Upstream tests mock with a real `GoogleAdsClient(credentials=AnonymousCredentials(),
  developer_token="fake-token", use_proto_plus=True)` for `get_type`, and `MagicMock` services.
- Versions resolved with the pin: fastmcp 4.2.3, google-ads 35.1.0, mcp 2.3.0. The google-ads
  client's default API version is v26, and upstream passes no version, so requests and
  services run on v26 at runtime; upstream's `v25` imports are type hints only.
- FastMCP 4.2.3 runs synchronous tools in a thread pool, so two tool calls from one client can
  overlap.
- Not in 0.0.6: `ads_mcp.audit` (on main since 2026-11-10).
