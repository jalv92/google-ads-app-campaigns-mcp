# Test-account run

The first run of the five tools against the real Google Ads API. It uses a Google Ads test
account: test accounts accept the same requests as real ones but never serve ads and never
spend money, so nothing can go wrong here that costs anything.

`scripts/test_account_run.py` drives the tools through the same in-process MCP client path a
real client uses. It refuses to start unless `customer.test_account` is true for the account.

## What you need

1. **A Google Cloud project with the Google Ads API enabled.** Enabling the API gives the
   project Test access, which is all this run needs. See
   [access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).
2. **A test manager account and a test client account under it.** Follow Google's guide to
   [test accounts](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts):
   create the test manager account first, then create the test client account from inside it.
   The manager ID is the `--login-customer-id`; the client ID is the `--customer-id`.
3. **An OAuth client** (Desktop app type) in that Cloud project, downloaded as a JSON file.
4. **Application Default Credentials** for the Google user that owns the test accounts:

   ```shell
   gcloud auth application-default login \
     --scopes https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/cloud-platform \
     --client-id-file=YOUR_CLIENT_JSON_FILE
   ```

   If the Cloud project is not the one gcloud picks up, also set `GOOGLE_PROJECT_ID`.
5. **A guardrails file for this run**, outside the repository, with `min_days_between_changes: 0`
   for the install range. Otherwise the campaign's own creation trips the cooldown and
   `set_target_cost` refuses (that refusal is covered by unit tests). The script checks this
   before it calls the API. Start from `guardrails.example.yaml`, put the test client ID in it,
   and set the install `min`/`max` to something like `0.80`/`1.50`.

## Run it

```shell
.venv/bin/python scripts/test_account_run.py \
  --customer-id TEST_CLIENT_ID \
  --login-customer-id TEST_MANAGER_ID \
  --guardrails PATH_TO_RUN_GUARDRAILS.yaml
```

`GOOGLE_ADS_APP_MCP_UNATTENDED` must not be set: the run commits create, enable and targeting
changes, which unattended mode forbids.

## What it does

Each write is a proposal (`validate_only=True`) followed by a commit with the returned checksum.

1. Reads `customer.test_account` and stops unless it is true.
2. Proposal only, never committed: a campaign with goal `INSTALLS_NO_TARGET`. This settles
   whether the API accepts an empty `maximize_conversions` with that goal.
3. Creates a PAUSED campaign (app `com.google.android.apps.adwords`, goal `INSTALLS_TARGET_COST`,
   daily budget 1.00, US and English), then replaces its headlines, adds a location, and moves
   the target cost inside the range.
4. Calls `days_since_target_change` for the campaign, retrying for up to fifteen minutes because
   change history lags (see Results). It must return a number, not `None`. Because the campaign's creation
   also counts, that alone does not prove the target change was logged, so the row "change_event
   logs the target change" then reads change history through `search_search`, retrying the
   same way, and requires a CAMPAIGN row with an UPDATE of the target cost.
5. Enables the campaign, then pauses it.
6. Runs the eight GAQL recipes of the playbook through the upstream `search_search` tool and
   reports, per recipe, whether the API accepted the query. (Test accounts have no metrics, so
   an accepted query can return no rows.)

It ends with one table: step, PROPOSED ok, COMMITTED ok, API message. A failed step does not
stop the others, and the table is printed even after an error. The campaign stays PAUSED in the
test account.

## Results

Run on 2026-11-20 against a Google Ads test client account under a test manager, with a Cloud
project at the Test access level. Two passes; the table is the second one, with account and
campaign IDs removed.

| Step | Proposed | Committed | Result |
|---|---|---|---|
| `customer.test_account` is true | n/a | n/a | read through `search_search` |
| Proposal only: `INSTALLS_NO_TARGET` (empty `maximize_conversions`) | yes | proposal only | validation passed |
| Create (PAUSED, `INSTALLS_TARGET_COST`) | yes | yes | campaign created paused |
| Update headlines | yes | yes | ad updated |
| Add a location | yes | yes | 1 added, 0 removed |
| `set_target_cost` 1.15 → 1.26 | yes | yes | target is now 1.26 USD per install |
| `days_since_target_change` returns a number | n/a | yes | 0.0 days |
| `change_event` logs the target change | n/a | yes, after a fix | see below |
| Enable | yes | yes | campaign enabled |
| Pause | yes | yes | campaign paused |
| GAQL recipes 1 to 8 | n/a | n/a | all eight accepted by the API |

Also checked by hand after the run, on the same campaign: with a limits file asking for 3 days
between changes, a new `set_target_cost` proposal was refused by the real API path with
"last change was 0.0 days ago; wait 3 days between changes".

What the run changed in this repository:

- **Change history is slower than documented.** Google documents up to three minutes; here the
  events were still missing after more than six minutes and present by sixteen. The script now
  waits up to fifteen minutes, and the server's in-memory record of its own commits is what
  covers that gap (see the README).
- **Recipe 6** selected `ad_group_ad_asset_view.policy_summary.approval_status`, which the API
  rejects as an unrecognised field. It now selects `ad_group_ad_asset_view.policy_summary`.
- **Recipe 8** used `DURING LAST_14_DAYS`, which ends yesterday and so never showed today's
  changes. It now names the dates.
- **Field masks through `search_search`.** `change_event.changed_fields` comes back as one
  comma-separated string in camelCase (a target change reads `targetCpa.targetCpaMicros`). The
  script's "change_event logs the target change" check expected snake_case and failed in both
  passes; it was fixed and then run again against both test campaigns, where it passed. The
  tools themselves read the API's own field paths and were not affected.
- A test account's status reads CLOSED in the API and in the Google Ads UI. That is normal.

Not covered by a test account: anything that needs serving data (metrics, asset performance
ratings, the learning status), and so most of the playbook's decision rules.
