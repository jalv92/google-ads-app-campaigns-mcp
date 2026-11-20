# Google Ads App Campaigns MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An installable MCP server that adds five App campaign tools, user-owned target-cost guardrails and a daily-review playbook on top of Google's official Google Ads MCP server.

**Architecture:** Our package depends on `google-ads-mcp==0.0.6`, imports its singleton server (`ads_mcp.coordinator.mcp`), mounts one extra FastMCP sub-server under the namespace `app_campaigns`, and hands over to upstream's `run_server()`. Every mutating tool goes through upstream's `propose_and_confirm`. Reads the tools need go through one small `queries.py`; the limits the user set live in one pure module, `guardrails.py`.

**Tech Stack:** Python ≥ 3.11, `google-ads-mcp==0.0.6` (brings fastmcp 4.2.3, google-ads 35.1.0 / API v25), PyYAML, pytest, black (`-l 80`), setuptools.

**Spec:** `docs/superpowers/specs/2026-11-20-google-ads-app-campaigns-mcp-design.md`. Verified API and upstream facts: `docs/research/2026-11-20-landscape-and-api-notes.md`. Raw Google doc pages: `tmp/google-docs/` (local only).

## Status (2026-11-20)

All seven tasks are implemented, each reviewed on its own, then reviewed as a whole branch and fixed in one wave. 172 unit tests pass against a mocked API. **The test-account run has been executed (2026-11-20): all five tools and all eight playbook queries work against the real Google Ads API.** Results and the three things the run changed are in `docs/test-account-run.md`. v1 is done.

Next, outside this plan: a real, serving account. It needs Explorer access on the Cloud project, the app's install conversions linked into Google Ads, the owner's own limits file and daily budget, and the OAuth consent screen moved out of Testing (a Testing app's sign-in expires after seven days, which would break a scheduled daily review).

Known limits left open on purpose:
- A second server process started inside the few minutes Google's change history lags does not know about the first one's target change. Parallel calls in one process are not locked either. Both are bounded by the user's range and step.
- A campaign created by this process can have its target changed before the history shows the creation; this needs a user in the conversation.
- A guardrails file that uses YAML merge keys (`<<:`) is refused with an unclear message.
- `create_app_campaign` does not check locally that location and language IDs are numeric; Google's validation refuses bad ones.
- CI installs an unpinned black, so a future black release could fail the format check.
- Not built: a scheduler, generated images, other campaign types, removing campaigns.

## Global Constraints

- `google-ads-mcp` pinned exactly: `==0.0.6`. Do not import `ads_mcp.audit` (absent in 0.0.6).
- Namespace `app_campaigns`; tool names exactly: `create_app_campaign`, `set_campaign_status`, `set_target_cost`, `update_app_ad`, `set_campaign_targeting`.
- Every mutating tool ends its signature with `login_customer_id: str | int | None = None, validate_only: bool = True, checksum: str | None = None`, returns `str`, and returns through `pac.propose_and_confirm(...)`.
- Only `ads_app_mcp/tools/create_campaign.py` may mention `CampaignBudget` / `campaign_budget`, and only to create one.
- `create_app_campaign` always sets campaign status `PAUSED`. `daily_budget` and `contains_eu_political_advertising` have no default.
- Guardrails: env `GOOGLE_ADS_APP_MCP_GUARDRAILS` → YAML path; values in account currency units, not micros; cooldown capped at 28 days; every failure to read or match refuses the change.
- Unattended: env `GOOGLE_ADS_APP_MCP_UNATTENDED` set to any value other than empty, `0`, `false` or `no` (any case, surrounding spaces ignored) → only `set_target_cost` and `update_app_ad` may commit. An unrecognised value counts as unattended.
- Money amounts cross the API as micros: `round(amount * 1_000_000)`. Compare in micros, never in floats.
- v1 campaign scope: sub-type `APP_CAMPAIGN` only. Any tool given another campaign type refuses before building a mutation.
- Errors the caller can fix are raised as `fastmcp.exceptions.ToolError` with a message that names the parameter.
- Tracked files are public-safe: no real customer IDs, app names, absolute paths or machine names. Tests use customer `1234567890`, campaign `111`, app `com.example.app`.
- Format with `black -l 80`. Tests: `.venv/bin/python -m pytest -q`.

## Review Focus

1. Customer ID given as `123-456-7890`, as `1234567890`, or written unquoted in the YAML (parsed as an int): all three must find the same guardrail entry. → Task 2.
2. A campaign that is not an App campaign (a Search campaign's ID) passed to any manage tool: refused, no mutation built. → Tasks 4 and 5.
3. A `set_target_cost` proposal confirmed after the target changed in between: the checksum still matches (it binds parameters only), so the commit path must re-read the current target and re-run every guardrail check. → Task 5.
4. `daily_budget` or `target_cost` of zero, negative, NaN or infinity; blank or duplicate headlines: refused locally with a clear message. → Tasks 3 and 4.
5. `GOOGLE_ADS_APP_MCP_UNATTENDED` set to `0`, `false` or empty must not switch unattended mode on. → Task 2.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml`, `LICENSE`, `NOTICE` | Packaging, Apache-2.0, credit to upstream. |
| `ads_app_mcp/server.py` | Mount our namespace on upstream's server; entry point. |
| `ads_app_mcp/tools/__init__.py` | `app_campaigns_mcp = FastMCP("app_campaigns")`. |
| `ads_app_mcp/guardrails.py` | Load and check the user's target-cost limits; unattended commit gate. Pure, no API. |
| `ads_app_mcp/queries.py` | The GAQL reads the tools need, returned as small dataclasses. |
| `ads_app_mcp/tools/create_campaign.py` | `create_app_campaign`. |
| `ads_app_mcp/tools/manage_campaign.py` | `set_campaign_status`, `set_campaign_targeting`, `set_target_cost`. |
| `ads_app_mcp/tools/app_ad.py` | `update_app_ad`. |
| `ads_app_mcp/playbook.md`, `ads_app_mcp/prompts.py` | Strategy resource and `daily_review` prompt. |
| `guardrails.example.yaml` | Example limits file. |
| `tests/conftest.py` | `fake_ads` fixture shared by all tool tests. |
| `scripts/test_account_run.py`, `docs/test-account-run.md` | Manual end-to-end run on a Google Ads test account. |

---

### Task 1: Package skeleton, mount and smoke test

**Files:**
- Create: `pyproject.toml`, `LICENSE`, `NOTICE`, `ads_app_mcp/__init__.py`, `ads_app_mcp/server.py`, `ads_app_mcp/tools/__init__.py`
- Test: `tests/__init__.py`, `tests/conftest.py`, `tests/test_server.py`, `tests/golden_tools.json`

**Interfaces:**
- Produces: `ads_app_mcp.tools.app_campaigns_mcp: FastMCP` (later tasks decorate tools on it with `@app_campaigns_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))`).
- Produces: `ads_app_mcp.server.build_server() -> FastMCP` (idempotent: imports every `ads_app_mcp.tools.*` module and `ads_app_mcp.prompts` if present, mounts once, returns upstream's `mcp`), `ads_app_mcp.server.run_server() -> None`.
- Produces the fixture `fake_ads` (in `tests/conftest.py`):

```python
class FakeAds:
    client: MagicMock            # get_type -> real GoogleAdsClient type factory; get_service(name, **kw) -> self.services[name]
    services: dict[str, MagicMock]   # created on first access, one MagicMock per service name
    real: GoogleAdsClient        # AnonymousCredentials, developer_token="fake-token", use_proto_plus=True

@pytest.fixture
def fake_ads(monkeypatch) -> FakeAds: ...   # patches ads_mcp.utils.get_googleads_client to return fake.client
```

- [x] **Step 1: Write `pyproject.toml`.** name `google-ads-app-campaigns-mcp`, version `0.1.0`, `requires-python = ">=3.11"`, `license = "Apache-2.0"`, dependencies `["google-ads-mcp==0.0.6", "pyyaml>=6"]`, extra `dev = ["pytest>=8", "black"]`, script `google-ads-app-campaigns-mcp = "ads_app_mcp.server:run_server"`, setuptools packages `ads_app_mcp`, `ads_app_mcp.tools`, package-data `ads_app_mcp = ["playbook.md"]`. Add the Apache-2.0 `LICENSE` text and a `NOTICE` saying the project builds on `googleads/google-ads-mcp` (Apache-2.0, © Google LLC) and is not affiliated with or endorsed by Google.
- [x] **Step 2: Install.** Run: `uv pip install --python .venv/bin/python -e ".[dev]"` — Expected: exits 0.
- [x] **Step 3: Write the failing tests** in `tests/test_server.py`:

```python
UPSTREAM = {"assets_create_asset", "assets_upload_video_asset",
            "customers_list_accessible_customers", "labels_manage_label_associations",
            "labels_manage_labels", "metadata_get_resource_metadata", "search_search"}

def test_tool_list_matches_golden():
    names = sorted(t.name for t in asyncio.run(build_server().list_tools()))
    assert names == json.load(open(GOLDEN))          # tests/golden_tools.json, a sorted list of names

def test_upstream_tools_are_exactly_the_known_seven():
    names = {t.name for t in asyncio.run(build_server().list_tools())}
    assert {n for n in names if not n.startswith("app_campaigns_")} == UPSTREAM

def test_build_server_is_idempotent():
    assert build_server() is build_server()
    names = [t.name for t in asyncio.run(build_server().list_tools())]
    assert len(names) == len(set(names))
```

`tests/golden_tools.json` starts as the seven upstream names; each later task adds its tool names to it.
- [x] **Step 4: Run** `.venv/bin/python -m pytest tests/test_server.py -q` — Expected: FAIL (`ads_app_mcp.server` missing).
- [x] **Step 5: Implement** `ads_app_mcp/tools/__init__.py` and `ads_app_mcp/server.py`. `build_server` uses `pkgutil.iter_modules` over `ads_app_mcp.tools` to import tool modules, then `mcp.mount(app_campaigns_mcp, namespace="app_campaigns")` guarded by a module-level flag. `run_server` calls `build_server()` then `ads_mcp.server.run_server()`.
- [x] **Step 6: Write `tests/conftest.py`** with `fake_ads` as specified above.
- [x] **Step 7: Run** `.venv/bin/python -m pytest -q` — Expected: 3 passed.
- [x] **Step 8: Commit** — `git add -A && git commit -m "feat: package skeleton mounted on upstream server"`

---

### Task 2: Guardrails and unattended gate

**Files:**
- Create: `ads_app_mcp/guardrails.py`, `guardrails.example.yaml`
- Test: `tests/test_guardrails.py`

**Interfaces:**
- Produces:

```python
GUARDRAILS_ENV = "GOOGLE_ADS_APP_MCP_GUARDRAILS"
UNATTENDED_ENV = "GOOGLE_ADS_APP_MCP_UNATTENDED"
UNATTENDED_COMMIT_ALLOWED = frozenset({"set_target_cost", "update_app_ad"})
MAX_COOLDOWN_DAYS = 28

class GoalKind(str, Enum): INSTALL = "install"; ACTION = "action"
class GuardrailError(Exception): ...

@dataclass(frozen=True)
class TargetRange:
    min_micros: int; max_micros: int; max_step_pct: float; min_days_between_changes: int

def load_range(customer_id: str | int, goal_kind: GoalKind, campaign_id: str | int | None = None) -> TargetRange
def check_initial_target(rng: TargetRange, target_micros: int) -> None
def check_target_change(rng: TargetRange, current_micros: int, new_micros: int, days_since_last_change: float | None) -> None
def is_unattended() -> bool
def ensure_commit_allowed(tool_name: str, validate_only: bool) -> None   # raises ToolError
```

- File schema (exactly this shape; all four keys required in every range):

```yaml
customers:
  "1234567890":
    defaults:
      install: {min: 0.80, max: 1.50, max_step_pct: 20, min_days_between_changes: 3}
      action:  {min: 2.00, max: 6.00, max_step_pct: 20, min_days_between_changes: 3}
    campaigns:
      "111": {min: 0.90, max: 1.20, max_step_pct: 15, min_days_between_changes: 5}
```

- Lookup: the campaign entry if `campaign_id` is given and present, else `defaults[goal_kind]`. Customer and campaign keys are compared as digit strings (`re.sub(r"\D", "", str(key))`).
- `check_target_change`: `days_since_last_change is None` means no change found in the window and passes the cooldown. Step is `abs(new - current) * 100 / current` and must be `<= max_step_pct`. `new == current` raises (nothing to change).

- [x] **Step 1: Write the failing tests** (each uses `tmp_path` + `monkeypatch.setenv`):

```python
def test_campaign_entry_wins_over_default()            # load_range("1234567890", INSTALL, "111").min_micros == 900_000
def test_default_used_when_no_campaign_entry()         # campaign "222" -> min_micros == 800_000
def test_customer_id_formats_match()                   # "123-456-7890", 1234567890 and an unquoted YAML key all resolve
def test_missing_env_refuses()                         # GuardrailError mentions GOOGLE_ADS_APP_MCP_GUARDRAILS
def test_missing_file_refuses()
def test_unknown_customer_refuses()
def test_missing_goal_default_refuses()                # file has only install; ACTION asked
@pytest.mark.parametrize("bad", [{"min": 0}, {"max": 0.5}, {"max_step_pct": 0}, {"max_step_pct": 101},
                                 {"min_days_between_changes": 29}, {"min_days_between_changes": -1}, {"min": "x"}])
def test_invalid_range_refuses(bad)                    # overrides one key of a valid range (max 0.5 < min 0.8)
def test_missing_key_refuses()
def test_initial_target_bounds()                       # 800_000 and 1_500_000 pass; 799_999 and 1_500_001 raise
def test_step_exactly_at_limit_passes()                # 1_000_000 -> 1_200_000 at 20 %
def test_step_over_limit_refuses()                     # 1_000_000 -> 1_200_001
def test_cooldown()                                    # 2.9 days raises, 3.0 passes, None passes (min_days 3)
def test_same_value_refuses()
@pytest.mark.parametrize("v,expected", [("1", True), ("true", True), ("YES", True), ("0", False), ("false", False), ("", False)])
def test_is_unattended(v, expected)
def test_unattended_blocks_other_commits()             # ensure_commit_allowed("set_campaign_status", False) raises ToolError
def test_unattended_allows_listed_commits_and_all_proposals()
def test_attended_allows_everything()
```

- [x] **Step 2: Run** `.venv/bin/python -m pytest tests/test_guardrails.py -q` — Expected: FAIL (module missing).
- [x] **Step 3: Implement `ads_app_mcp/guardrails.py`** to the interfaces above. Read the file on every call (no cache: the user edits it between runs). Reject non-finite numbers.
- [x] **Step 4: Write `guardrails.example.yaml`** — the schema above with a comment per key in plain words, and the note that the real file lives outside any repo.
- [x] **Step 5: Run** `.venv/bin/python -m pytest -q` — Expected: all pass.
- [x] **Step 6: Commit** — `git commit -am "feat: target-cost guardrails and unattended commit gate"` (after `git add` of new files).

---

### Task 3: Queries and `create_app_campaign`

**Files:**
- Create: `ads_app_mcp/queries.py`, `ads_app_mcp/tools/create_campaign.py`
- Modify: `tests/golden_tools.json` (add `app_campaigns_create_app_campaign`)
- Test: `tests/test_queries.py`, `tests/test_create_campaign.py`, `tests/test_budget_invariant.py`

**Interfaces:**
- Consumes: `app_campaigns_mcp`, `fake_ads` (Task 1); `guardrails.load_range`, `check_initial_target`, `ensure_commit_allowed`, `GoalKind`, `GuardrailError` (Task 2).
- Produces in `queries.py` (each takes `client` from `utils.get_googleads_client` and a cleaned `customer_id: str`):

```python
@dataclass(frozen=True)
class CampaignInfo:
    campaign_id: str; name: str; status: str; goal_type: str
    target_cost_micros: int | None; currency_code: str
    @property
    def goal_kind(self) -> GoalKind | None    # INSTALL for *_TARGET_INSTALL_COST, ACTION for *_TARGET_CONVERSION_COST, else None

def get_currency_code(client, customer_id: str) -> str
def get_app_campaign(client, customer_id: str, campaign_id: str | int) -> CampaignInfo
    # ToolError when no row, or the row is not MULTI_CHANNEL + APP_CAMPAIGN
```

  Queries run with `GoogleAdsService.search(customer_id=..., query=...)`. `target_cost_micros` is `None` when `campaign.target_cpa.target_cpa_micros` is 0/unset.
- Produces in `create_campaign.py`:

```python
class Goal(str, Enum):
    INSTALLS_TARGET_COST = "INSTALLS_TARGET_COST"            # OPTIMIZE_INSTALLS_TARGET_INSTALL_COST, target_cpa, GoalKind.INSTALL
    INSTALLS_NO_TARGET = "INSTALLS_NO_TARGET"                # OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST, empty maximize_conversions
    IN_APP_ACTIONS_TARGET_COST = "IN_APP_ACTIONS_TARGET_COST"  # OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST, target_cpa, GoalKind.ACTION
class AppStore(str, Enum): GOOGLE_APP_STORE = "GOOGLE_APP_STORE"; APPLE_APP_STORE = "APPLE_APP_STORE"

def create_app_campaign(
    customer_id: str | int, name: str, app_id: str, daily_budget: float,
    contains_eu_political_advertising: bool, goal: Goal,
    headlines: list[str], descriptions: list[str],
    target_cost: float | None = None, app_store: AppStore = AppStore.GOOGLE_APP_STORE,
    conversion_actions: list[str] | None = None,      # conversion action IDs or resource names
    location_ids: list[str | int] | None = None,      # geo target constant IDs, e.g. 2840
    language_ids: list[str | int] | None = None,      # language constant IDs, e.g. 1000
    image_assets: list[str] | None = None,            # asset IDs or resource names
    youtube_video_assets: list[str] | None = None,
    login_customer_id: str | int | None = None, validate_only: bool = True, checksum: str | None = None,
) -> str

def validate_ad_texts(headlines: list[str] | None, descriptions: list[str] | None,
                      image_assets: list[str] | None, youtube_video_assets: list[str] | None) -> None
    # ToolError unless: 2–5 headlines, 1–5 descriptions, <= 20 images, <= 20 videos,
    # no blank strings, no duplicates within a list. A None list is skipped (Task 4 reuses this).
```

- Request shape: one `MutateGoogleAdsRequest` to `GoogleAdsService.mutate`, operations in this order — `campaign_budget_operation.create` (resource name `customers/{cid}/campaignBudgets/-1`, `amount_micros`, `explicitly_shared = False`, name `"{name} budget"`), `campaign_operation.create` (`-2`, status `PAUSED`, `MULTI_CHANNEL`, `APP_CAMPAIGN`, `app_campaign_setting`, bidding per goal, `contains_eu_political_advertising` enum, `selective_optimization.conversion_actions` when given), one `campaign_criterion_operation.create` per location and language, `ad_group_operation.create` (`-3`, name `"{name} ad group"`, `type` unset), `ad_group_ad_operation.create` (`ad.app_ad`: inline `AdTextAsset` for texts, `AdImageAsset`/`AdVideoAsset` by resource name). `validate_fn` sends it with `validate_only=True`; `commit_fn` sends it for real and returns a message with the new campaign ID.
- Rules: `target_cost` required for the two target goals and rejected for `INSTALLS_NO_TARGET`; `IN_APP_ACTIONS_TARGET_COST` requires `conversion_actions`; for target goals `load_range(customer, goal_kind)` then `check_initial_target` (a `GuardrailError` becomes a `ToolError`); `ensure_commit_allowed("create_app_campaign", validate_only)` first.
- Change items (via `pac.build_change_items("CREATE", "App campaign", name, fields)`) include, in words a non-developer reads: `"Daily budget": "5.00 USD"`, `"Target cost": "1.20 USD per install"` (or `"per in-app action"`, or `"none (Google sets the cost)"`), `"Status": "PAUSED"`, app, store, EU political ads declaration, locations, languages, headlines, descriptions, image and video counts.
- `operation_payload` for the checksum: a dict of every tool parameter except `validate_only`, `checksum`.

- [x] **Step 1: Write the failing tests.**

```python
# tests/test_queries.py
def test_get_app_campaign_maps_row(fake_ads)                 # row with target 1_200_000, OPTIMIZE_INSTALLS_TARGET_INSTALL_COST -> goal_kind INSTALL
def test_get_app_campaign_refuses_other_types(fake_ads)      # SEARCH row -> ToolError mentioning "App campaign"
def test_get_app_campaign_refuses_missing(fake_ads)          # no rows -> ToolError
def test_goal_kind_none_without_target(fake_ads)

# tests/test_create_campaign.py  (helper: ops(fake_ads) -> list of mutate_operations from the last GoogleAdsService.mutate call)
def test_proposal_builds_expected_operations(fake_ads, guardrails_file)
    # order and kinds: budget, campaign, 1 location, 1 language, ad group, ad group ad
    # budget.amount_micros == 5_000_000; explicitly_shared is False
    # campaign.status PAUSED; channel MULTI_CHANNEL; sub type APP_CAMPAIGN; app_id "com.example.app"
    # campaign.target_cpa.target_cpa_micros == 1_200_000; campaign_budget == ".../campaignBudgets/-1"
    # request.validate_only is True; result JSON status == "PROPOSED" and has a checksum
def test_change_items_show_money_with_currency(fake_ads, guardrails_file)   # "5.00 USD", "1.20 USD per install"
def test_commit_requires_matching_checksum(fake_ads, guardrails_file)
def test_commit_sends_validate_only_false_and_reports_id(fake_ads, guardrails_file)
def test_no_target_goal_uses_maximize_conversions(fake_ads)                 # and needs no guardrails file
def test_target_goal_without_guardrails_refuses(fake_ads)                   # ToolError, GoogleAdsService.mutate not called
def test_target_outside_range_refuses(fake_ads, guardrails_file)            # 1.60 with max 1.50
def test_target_cost_rules(fake_ads, guardrails_file)                       # missing for target goal; given for no-target goal
def test_in_app_goal_requires_conversion_actions(fake_ads, guardrails_file)
@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf")])
def test_bad_budget_refuses(fake_ads, budget)
@pytest.mark.parametrize("headlines", [["one"], ["a"] * 6, ["a", " "], ["same", "same"]])
def test_bad_headlines_refuse(fake_ads, headlines)
def test_unattended_blocks_commit_but_not_proposal(fake_ads, guardrails_file, monkeypatch)

# tests/test_budget_invariant.py
def test_only_create_campaign_mentions_budgets()
    # scan every .py under ads_app_mcp/: "campaign_budget" (case-insensitive, also "CampaignBudget")
    # appears in tools/create_campaign.py only
def test_create_builds_exactly_one_budget_create(fake_ads, guardrails_file)
    # exactly one operation has campaign_budget_operation set; its "create" is populated, "update"/"remove" are not
```

  `guardrails_file` is a fixture added to `tests/conftest.py`: writes the Task 2 example to `tmp_path`, sets the env var.
- [x] **Step 2: Run** `.venv/bin/python -m pytest tests/test_queries.py tests/test_create_campaign.py tests/test_budget_invariant.py -q` — Expected: FAIL (modules missing).
- [x] **Step 3: Implement `queries.py`** (the two functions and the dataclass).
- [x] **Step 4: Implement `create_campaign.py`** to the interfaces above. The tool docstring is what the model reads: state the propose/confirm steps as upstream's `manage_labels` does, that the campaign is created paused, and that `daily_budget` must be the amount the user said, never a suggestion of the model's.
- [x] **Step 5: Run** `.venv/bin/python -m pytest -q` — Expected: all pass (golden list updated).
- [x] **Step 6: Commit** — `feat: create_app_campaign with guardrailed target and budget invariant`

---

### Task 4: `set_campaign_status`, `set_campaign_targeting`, `update_app_ad`

**Files:**
- Create: `ads_app_mcp/tools/manage_campaign.py`, `ads_app_mcp/tools/app_ad.py`
- Modify: `ads_app_mcp/queries.py`, `tests/golden_tools.json`
- Test: `tests/test_manage_campaign.py`, `tests/test_app_ad.py`, extend `tests/test_queries.py`

**Interfaces:**
- Consumes: `get_app_campaign`, `CampaignInfo`, `validate_ad_texts` (Task 3); `ensure_commit_allowed` (Task 2).
- Produces in `queries.py`:

```python
@dataclass(frozen=True)
class AppAd:
    ad_group_id: str; ad_resource_name: str
    headlines: list[str]; descriptions: list[str]; image_assets: list[str]; youtube_video_assets: list[str]

def get_app_ads(client, customer_id: str, campaign_id: str | int) -> list[AppAd]       # one per ad group, non-removed
def get_targeting(client, customer_id: str, campaign_id: str | int) -> dict[str, dict[str, str]]
    # {"location": {"2840": "<campaign criterion resource name>"}, "language": {"1000": "..."}}; positive criteria only
```

- Produces the tools:

```python
class CampaignStatus(str, Enum): ENABLED = "ENABLED"; PAUSED = "PAUSED"

def set_campaign_status(customer_id, campaign_id: str | int, status: CampaignStatus, <common tail>) -> str
    # CampaignService.mutate_campaigns, update mask ["status"]; ToolError when already in that status

def set_campaign_targeting(customer_id, campaign_id: str | int,
    add_location_ids: list[str | int] | None = None, remove_location_ids: list[str | int] | None = None,
    add_language_ids: list[str | int] | None = None, remove_language_ids: list[str | int] | None = None,
    <common tail>) -> str
    # CampaignCriterionService.mutate_campaign_criteria; ToolError when nothing to do,
    # when adding an ID already targeted, or removing one that is not targeted

def update_app_ad(customer_id, campaign_id: str | int,
    headlines: list[str] | None = None, descriptions: list[str] | None = None,
    image_assets: list[str] | None = None, youtube_video_assets: list[str] | None = None,
    ad_group_id: str | int | None = None, <common tail>) -> str
    # each list given REPLACES that list; None leaves it as is; at least one list required
    # AdService.mutate_ads, update mask "app_ad.headlines" etc. only for the lists given
    # several ad groups and no ad_group_id -> ToolError listing the ad group IDs
```

- All three call `get_app_campaign` first (refuses non-App campaigns), `ensure_commit_allowed(<tool name>, validate_only)`, and show before → after in the change items through `original_fields`.

- [x] **Step 1: Write the failing tests.**

```python
# tests/test_manage_campaign.py
def test_status_proposal_shows_before_and_after(fake_ads)          # PAUSED -> ENABLED in change_items
def test_status_commit_updates_only_status(fake_ads)               # update_mask.paths == ["status"]
def test_status_same_value_refuses(fake_ads)
def test_status_refuses_non_app_campaign(fake_ads)                 # mutate_campaigns not called
def test_status_commit_blocked_when_unattended(fake_ads, monkeypatch)
def test_targeting_adds_and_removes(fake_ads)                      # 1 create with geoTargetConstants/2124, 1 remove with the existing resource name
def test_targeting_refuses_unknown_removal(fake_ads)
def test_targeting_refuses_duplicate_add(fake_ads)
def test_targeting_requires_a_change(fake_ads)
def test_targeting_commit_blocked_when_unattended(fake_ads, monkeypatch)

# tests/test_app_ad.py
def test_replaces_only_given_lists(fake_ads)                       # headlines given -> mask == ["app_ad.headlines"]; descriptions untouched
def test_change_items_show_old_and_new_texts(fake_ads)
def test_requires_at_least_one_list(fake_ads)
def test_validates_counts(fake_ads)                                # one headline -> ToolError, mutate_ads not called
def test_multiple_ad_groups_need_ad_group_id(fake_ads)
def test_commit_allowed_when_unattended(fake_ads, monkeypatch)
def test_refuses_non_app_campaign(fake_ads)
```

- [x] **Step 2: Run** the three test files — Expected: FAIL (modules missing).
- [x] **Step 3: Implement** the two query functions, then the three tools.
- [x] **Step 4: Run** `.venv/bin/python -m pytest -q` — Expected: all pass, including `test_only_create_campaign_mentions_budgets`.
- [x] **Step 5: Commit** — `feat: status, targeting and app ad tools`

---

### Task 5: `set_target_cost`

**Files:**
- Modify: `ads_app_mcp/tools/manage_campaign.py`, `ads_app_mcp/queries.py`, `tests/golden_tools.json`
- Test: `tests/test_target_cost.py`, extend `tests/test_queries.py`

**Interfaces:**
- Consumes: `get_app_campaign`, `CampaignInfo.goal_kind` (Task 3); `load_range`, `check_target_change`, `MAX_COOLDOWN_DAYS`, `GuardrailError`, `ensure_commit_allowed` (Task 2).
- Produces:

```python
# queries.py
def days_since_target_change(client, customer_id: str, campaign_id: str | int, now: datetime | None = None) -> float | None
    # Reads change_event for this campaign over the last MAX_COOLDOWN_DAYS + 1 days (the resource
    # requires a date range and a LIMIT). Returns days since the newest event that created the
    # campaign or whose changed_fields touch "target_cpa"; None when there is none.
    # Any API error propagates (the caller refuses).

# manage_campaign.py
def set_target_cost(customer_id, campaign_id: str | int, target_cost: float, <common tail>) -> str
    # CampaignService.mutate_campaigns, update mask ["target_cpa.target_cpa_micros"]
```

- Order inside `set_target_cost`, on the proposal **and again on the commit**: `get_app_campaign` → refuse when `goal_kind is None` (no target to move) → `load_range(customer, goal_kind, campaign_id)` → `days_since_target_change` (an exception becomes a `ToolError` saying the change history could not be read) → `check_target_change`. Then `propose_and_confirm`.
- Change items: `"Target cost": "1.20 USD per install"` with original `"1.00 USD per install"`, plus a `"Allowed range"` line (`"0.80–1.50 USD, max 20 % per change, 3 days between changes"`).

- [x] **Step 1: Write the failing tests.**

```python
# tests/test_queries.py
def test_days_since_target_change_picks_newest_target_event(fake_ads)   # events: status change 1 day ago, target change 4 days ago -> ~4.0
def test_days_since_target_change_counts_campaign_creation(fake_ads)
def test_days_since_target_change_none_when_no_event(fake_ads)
def test_days_since_target_change_propagates_api_error(fake_ads)

# tests/test_target_cost.py
def test_proposal_within_limits(fake_ads, guardrails_file)              # 1.00 -> 1.20, mask == ["target_cpa.target_cpa_micros"], micros 1_200_000
def test_out_of_range_refuses(fake_ads, guardrails_file)                # 1.60
def test_step_too_large_refuses(fake_ads, guardrails_file)              # 1.00 -> 1.30
def test_cooldown_refuses(fake_ads, guardrails_file)                    # last change 1 day ago, min 3
def test_unreadable_history_refuses(fake_ads, guardrails_file)          # search raises -> ToolError, mutate not called
def test_no_guardrails_refuses(fake_ads)
def test_campaign_without_target_refuses(fake_ads, guardrails_file)
def test_non_app_campaign_refuses(fake_ads, guardrails_file)
def test_action_goal_uses_action_range(fake_ads, guardrails_file)       # 2.00–6.00 applies, 1.20 refused
def test_commit_rechecks_current_state(fake_ads, guardrails_file)
    # propose at current 1.00 -> 1.20; before commit the fake returns current 0.90;
    # commit with the valid checksum raises ToolError (step now 33 %), mutate not called with validate_only False
def test_commit_allowed_when_unattended(fake_ads, guardrails_file, monkeypatch)
```

- [x] **Step 2: Run** — Expected: FAIL.
- [x] **Step 3: Implement** `days_since_target_change`, then `set_target_cost`.
- [x] **Step 4: Run** `.venv/bin/python -m pytest -q` — Expected: all pass.
- [x] **Step 5: Commit** — `feat: guardrailed set_target_cost with cooldown from change history`

---

### Task 6: Playbook and `daily_review` prompt

**Files:**
- Create: `ads_app_mcp/playbook.md`, `ads_app_mcp/prompts.py`
- Modify: `ads_app_mcp/server.py` (import `ads_app_mcp.prompts` in `build_server`)
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: upstream's `ads_mcp.coordinator.mcp` (register on it directly, as upstream's resources do).
- Produces: resource `resource://app-campaign-playbook` (text of `playbook.md`, read with `importlib.resources`); prompt `daily_review(customer_id: str, campaign_id: str | None = None)`.

**Playbook content** (`playbook.md`), in this order:
1. *How to read an App campaign* — the GAQL recipes, each a runnable query for upstream's `search_search`: daily spend / installs / cost per install for yesterday, last 7 and last 30 days; budget amount and how much of it was spent; conversions by `segments.conversion_action`; per-asset performance from `ad_group_ad_asset_view` including its performance label; `app_top_combination_view`; the last 14 days of `change_event` for the campaign. Field names are checked against the google-ads v25 protos installed in `.venv` before they are written down.
2. *Decision rules* — when the campaign is still learning and must be left alone; when an asset has enough data to be judged and replaced; how the target cost should move (direction, never beyond the guardrails, which the tool enforces anyway); when the budget is the limiting factor. **Every number carries a link to the Google Ads Help or developer page it came from, or the words "adjustable default" and one line of reasoning.** No number without one of the two.
3. *What the agent may do alone and what waits for the user* — mirrors the unattended allowlist; budget is never changed, only recommended in words.
4. *Report format* — the fixed sections of the daily report: Summary (3 lines) · Numbers (yesterday / 7 d / 30 d) · Changes applied today and why · Proposals waiting for you · Budget note (only when budget is the limit).

**Prompt content** (`daily_review`): tells the model to read the playbook resource, run the recipes for the given customer (and campaign, or every enabled App campaign), apply only what the playbook and the tools allow, and answer in the report format. It states plainly that tool refusals from guardrails are final and must be reported, not worked around.

- [x] **Step 1: Write the failing tests.**

```python
def test_playbook_resource_is_listed()                 # "resource://app-campaign-playbook" in list_resources()
def test_daily_review_prompt_is_listed()               # "daily_review" in [p.name for p in list_prompts()]
def test_daily_review_mentions_customer_and_playbook() # rendered text contains the customer id and the resource URI
def test_every_number_rule_has_source_or_default()
    # every bullet under "## Decision rules" that contains a digit also contains "http" or "adjustable default"
def test_playbook_recipes_only_select()                # every fenced sql block starts with SELECT
```

- [x] **Step 2: Run** — Expected: FAIL.
- [x] **Step 3: Research and write `playbook.md`.** Sources to read first: Google Ads Help "About App campaigns", "App campaign best practices" / optimization guidance, asset reporting and performance ratings, and the developer pages in `tmp/google-docs/`. Record each URL used.
- [x] **Step 4: Implement `prompts.py`** and the import in `build_server`.
- [x] **Step 5: Run** `.venv/bin/python -m pytest -q` — Expected: all pass.
- [x] **Step 6: Commit** — `feat: strategy playbook resource and daily_review prompt`

---

### Task 7: README and test-account run

**Files:**
- Create: `README.md`, `scripts/test_account_run.py`, `docs/test-account-run.md`

**Interfaces:**
- Consumes: the five tools through `build_server()` and FastMCP's in-process client, so the run exercises the same path an MCP client does.
- Produces: `python scripts/test_account_run.py --customer-id <test account> [--login-customer-id <test manager>] --guardrails <path>`. It refuses to start unless `customer.test_account` is true for the account. Steps, each printing the proposal and then the commit result: create (app `com.google.android.apps.adwords`, goal `INSTALLS_TARGET_COST`, budget 1.00, US + English) → update headlines → add a location → move the target inside the range → enable → pause. The guardrails file used for this run sets `min_days_between_changes: 0`, otherwise the campaign's own creation trips the cooldown (that refusal is covered by unit tests). Ends with a table: step, PROPOSED ok, COMMITTED ok, API message.

- [x] **Step 1: Write `scripts/test_account_run.py`** as specified. No test for the script itself; it is the test.
- [x] **Step 2: Write `docs/test-account-run.md`** — how to get a test manager account and a test client account, the `gcloud auth application-default login` command with the adwords scope, the command line, and a **Results** section left with the line `Not run yet.`
- [x] **Step 3: Write `README.md`.** Sections: what this adds to the official server (table of the five tools) · the money boundary in plain words, including what it does **not** protect against · install and MCP client config (`pipx run google-ads-app-campaigns-mcp`, env vars) · one-time Google setup (Cloud project, enable the API, Explorer access, OAuth client, ADC login; linking Play/Firebase so install conversions exist) · the guardrails file · the daily review and one scheduling example · status: which tools have been run against a real API and which have not · upstream's telemetry-header notice · license and non-affiliation.
- [x] **Step 4: Run** `.venv/bin/python -m pytest -q && .venv/bin/black -l 80 --check ads_app_mcp tests scripts` — Expected: all pass, nothing to reformat.
- [x] **Step 5: Commit** — `docs: README and test-account run`

**After Task 7 (needs the owner's Google setup, not part of this plan's code):** run `scripts/test_account_run.py`, paste the table into `docs/test-account-run.md` → Results, and update the README status section. v1 is done when that has happened.
