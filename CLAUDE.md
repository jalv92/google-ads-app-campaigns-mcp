# google-ads-app-campaigns-mcp

An MCP server that adds App campaign management to Google's official Google Ads MCP server.
It installs upstream as a dependency and mounts five more tools next to upstream's.

Read first: `docs/superpowers/specs/2026-11-20-google-ads-app-campaigns-mcp-design.md` (what and
why), then `docs/superpowers/plans/` (current work), then `docs/research/` (verified facts).

## Rules that must not bend

- **Budget.** Only `create_app_campaign` may build a `CampaignBudgetOperation`, and only a
  create. No tool changes a budget afterwards. `tests/test_budget_invariant.py` guards this.
- **Target cost.** `set_target_cost` goes through `ads_app_mcp/guardrails.py` and fails closed.
- **Unattended mode.** Only `set_target_cost` and `update_app_ad` may commit.
- **No invented numbers** in `ads_app_mcp/playbook.md`: cite the Google source or label the
  value an adjustable default.
- **This repo is public.** Nothing personal or internal in tracked files: no account or
  customer IDs, no app names, no absolute paths, no machine names. Working notes go in `tmp/`
  (gitignored). The user's `guardrails.yaml` and credentials never live here.

## Releases

- Public since 2026-11-20. The public history starts at a single root commit; the private
  pre-release history is not published.
- Not on PyPI yet. The README installs from the git repository; update its commands when a
  PyPI release exists.

## Upstream pin

`google-ads-mcp` is pinned exactly. To bump it: re-read upstream's
`coordinator.initialize_and_mount_tools` and mirror any new per-sub-server step in
`ads_app_mcp/server.py` (main already adds audit instrumentation that 0.0.6 lacks), re-check
the signatures listed in `docs/research/`, update `tests/golden_tools.json`, and read every
new upstream tool for anything that can touch a budget or a bid.

## Working here

- Python ≥ 3.11. `uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"`
- Tests: `.venv/bin/python -m pytest -q`
- Format: `.venv/bin/black -l 80 ads_app_mcp tests scripts`
- Everything written here is in English.
