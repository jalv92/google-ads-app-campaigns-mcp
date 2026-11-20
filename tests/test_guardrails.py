import pytest
from fastmcp.exceptions import ToolError

from ads_app_mcp import guardrails as g
from ads_app_mcp.guardrails import GoalKind, GuardrailError

VALID = """
customers:
  "1234567890":
    defaults:
      install: {min: 0.80, max: 1.50, max_step_pct: 20, min_days_between_changes: 3}
      action:  {min: 2.00, max: 6.00, max_step_pct: 20, min_days_between_changes: 3}
    campaigns:
      "111": {min: 0.90, max: 1.20, max_step_pct: 15, min_days_between_changes: 5}
"""


def use(tmp_path, monkeypatch, text=VALID):
    p = tmp_path / "guardrails.yaml"
    p.write_text(text)
    monkeypatch.setenv(g.GUARDRAILS_ENV, str(p))


@pytest.fixture
def rng(tmp_path, monkeypatch):
    use(tmp_path, monkeypatch)
    return g.load_range("1234567890", GoalKind.INSTALL)


def test_campaign_entry_wins_over_default(tmp_path, monkeypatch):
    use(tmp_path, monkeypatch)
    r = g.load_range("1234567890", GoalKind.INSTALL, "111")
    assert r.min_micros == 900_000
    assert r.max_step_pct == 15
    assert r.min_days_between_changes == 5


def test_campaign_id_may_be_a_resource_name(tmp_path, monkeypatch):
    use(tmp_path, monkeypatch)
    rn = "customers/1234567890/campaigns/111"
    assert g.load_range("1234567890", GoalKind.INSTALL, rn).min_micros == (
        900_000
    )


def test_default_used_when_no_campaign_entry(tmp_path, monkeypatch):
    use(tmp_path, monkeypatch)
    r = g.load_range("1234567890", GoalKind.INSTALL, "222")
    assert r.min_micros == 800_000


def test_customer_id_formats_match(tmp_path, monkeypatch):
    use(tmp_path, monkeypatch)
    for cid in ("123-456-7890", 1234567890, "1234567890"):
        assert g.load_range(cid, GoalKind.INSTALL).max_micros == 1_500_000
    use(tmp_path, monkeypatch, VALID.replace('"1234567890"', "1234567890"))
    assert g.load_range("1234567890", GoalKind.INSTALL).max_micros == 1_500_000


def test_missing_env_refuses(monkeypatch):
    monkeypatch.delenv(g.GUARDRAILS_ENV, raising=False)
    with pytest.raises(GuardrailError, match="GOOGLE_ADS_APP_MCP_GUARDRAILS"):
        g.load_range("1234567890", GoalKind.INSTALL)


def test_missing_file_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv(g.GUARDRAILS_ENV, str(tmp_path / "nope.yaml"))
    with pytest.raises(GuardrailError):
        g.load_range("1234567890", GoalKind.INSTALL)


def test_unknown_customer_refuses(tmp_path, monkeypatch):
    use(tmp_path, monkeypatch)
    with pytest.raises(GuardrailError):
        g.load_range("9999999999", GoalKind.INSTALL)


def test_missing_goal_default_refuses(tmp_path, monkeypatch):
    text = "customers:\n  '1234567890':\n    defaults:\n"
    text += "      install: {min: 0.8, max: 1.5, max_step_pct: 20, "
    text += "min_days_between_changes: 3}\n"
    use(tmp_path, monkeypatch, text)
    with pytest.raises(GuardrailError):
        g.load_range("1234567890", GoalKind.ACTION)


@pytest.mark.parametrize(
    "bad",
    [
        {"min": 0},
        {"max": 0.5},
        {"max_step_pct": 0},
        {"max_step_pct": 101},
        {"min_days_between_changes": 29},
        {"min_days_between_changes": -1},
        {"min": "x"},
        {"max": float("inf")},
        {"min": float("nan")},
    ],
)
def test_invalid_range_refuses(tmp_path, monkeypatch, bad):
    rng = {
        "min": 0.8,
        "max": 1.5,
        "max_step_pct": 20,
        "min_days_between_changes": 3,
    } | bad
    body = ", ".join(f"{k}: {v!r}" for k, v in rng.items())
    text = f"customers:\n  '1234567890':\n    defaults:\n      install: {{{body}}}\n"
    text = text.replace("inf", ".inf").replace("nan", ".nan")
    use(tmp_path, monkeypatch, text)
    with pytest.raises(GuardrailError):
        g.load_range("1234567890", GoalKind.INSTALL)


def test_missing_key_refuses(tmp_path, monkeypatch):
    text = (
        "customers:\n  '1234567890':\n    defaults:\n"
        "      install: {min: 0.8, max: 1.5, max_step_pct: 20}\n"
    )
    use(tmp_path, monkeypatch, text)
    with pytest.raises(GuardrailError):
        g.load_range("1234567890", GoalKind.INSTALL)


def test_initial_target_bounds(rng):
    g.check_initial_target(rng, 800_000)
    g.check_initial_target(rng, 1_500_000)
    for v in (799_999, 1_500_001):
        with pytest.raises(GuardrailError):
            g.check_initial_target(rng, v)


def test_step_exactly_at_limit_passes(rng):
    g.check_target_change(rng, 1_000_000, 1_200_000, None)


def test_step_over_limit_refuses(rng):
    with pytest.raises(GuardrailError):
        g.check_target_change(rng, 1_000_000, 1_200_001, None)


def test_target_change_outside_range_refuses(rng):
    with pytest.raises(GuardrailError):
        g.check_target_change(rng, 1_450_000, 1_550_000, None)


def test_cooldown(rng):
    with pytest.raises(GuardrailError):
        g.check_target_change(rng, 1_000_000, 1_100_000, 2.9)
    g.check_target_change(rng, 1_000_000, 1_100_000, 3.0)
    g.check_target_change(rng, 1_000_000, 1_100_000, None)


def test_same_value_refuses(rng):
    with pytest.raises(GuardrailError):
        g.check_target_change(rng, 1_000_000, 1_000_000, None)


@pytest.mark.parametrize(
    "v,expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("y", True),
        ("2", True),
        ("enabled", True),
        ("0", False),
        ("false", False),
        (" FALSE ", False),
        ("no", False),
        ("", False),
        ("   ", False),
    ],
)
def test_is_unattended(monkeypatch, v, expected):
    monkeypatch.setenv(g.UNATTENDED_ENV, v)
    assert g.is_unattended() is expected


def test_unattended_blocks_other_commits(monkeypatch):
    monkeypatch.setenv(g.UNATTENDED_ENV, "1")
    with pytest.raises(ToolError):
        g.ensure_commit_allowed("set_campaign_status", False)


def test_unattended_allows_listed_commits_and_all_proposals(monkeypatch):
    monkeypatch.setenv(g.UNATTENDED_ENV, "1")
    g.ensure_commit_allowed("set_target_cost", False)
    g.ensure_commit_allowed("update_app_ad", False)
    g.ensure_commit_allowed("set_campaign_status", True)
    g.ensure_commit_allowed("create_app_campaign", True)


def test_attended_allows_everything(monkeypatch):
    monkeypatch.delenv(g.UNATTENDED_ENV, raising=False)
    g.ensure_commit_allowed("set_campaign_status", False)


def _cust(body):
    return "customers:\n  '1234567890':\n" + body


GOOD = "{min: 0.8, max: 1.5, max_step_pct: 20, min_days_between_changes: 3}"


@pytest.mark.parametrize(
    "body",
    [
        f"    defaults:\n      install: {GOOD}\n    campaigns:\n      - '111'\n",
        f"    defaults:\n      install: {GOOD}\n    campaign:\n      '111': {GOOD}\n",
        f"    defaults: [install]\n",
        f"    defaults:\n      install: {GOOD}\n      instal: {GOOD}\n",
        "    defaults:\n      install: {min: 0.8, max: 1.5, max_step_pct: 20, "
        "min_days_between_changes: 3, extra: 1}\n",
    ],
)
def test_malformed_structure_refuses(tmp_path, monkeypatch, body):
    use(tmp_path, monkeypatch, _cust(body))
    with pytest.raises(GuardrailError):
        g.load_range("1234567890", GoalKind.INSTALL, "111")


C = '  "1234567890":\n    defaults:\n      install: ' + GOOD + "\n"


def _camps(*keys):
    return "    campaigns:\n" + "".join(f"      {k}: {GOOD}\n" for k in keys)


@pytest.mark.parametrize(
    "text,campaign_id,match",
    [
        (
            C + C.replace('"1234567890"', '"123-456-7890"'),
            None,
            "more than once",
        ),
        (C + _camps("'111'", "111"), "111", "more than once"),
        (C + C, None, "duplicate key"),
        (C + _camps("'111'", "'111'"), "111", "duplicate key"),
        (C + "  abc:\n    defaults: {}\n", None, "no digits"),
        (C + _camps("abc"), None, "no digits"),
        (
            C + C.replace('"1234567890"', '"999"') + _camps("abc"),
            "111",
            "no digits",
        ),
    ],
    ids=[
        "customer-two-spellings",
        "campaign-two-spellings",
        "customer-literal-duplicate",
        "campaign-literal-duplicate",
        "digitless-customer-after-match",
        "digitless-campaign-no-lookup",
        "digitless-campaign-other-customer",
    ],
)
def test_ambiguous_or_digitless_keys_refuse(
    tmp_path, monkeypatch, text, campaign_id, match
):
    use(tmp_path, monkeypatch, "customers:\n" + text)
    with pytest.raises(GuardrailError, match=match):
        g.load_range("1234567890", GoalKind.INSTALL, campaign_id)


def test_days_error_names_the_key_the_user_wrote(tmp_path, monkeypatch):
    use(
        tmp_path,
        monkeypatch,
        VALID.replace(
            "min_days_between_changes: 3", "min_days_between_changes: x", 1
        ),
    )
    with pytest.raises(
        GuardrailError, match=r"\.min_days_between_changes must"
    ):
        g.load_range("1234567890", GoalKind.INSTALL)


def test_non_utf8_file_refuses(tmp_path, monkeypatch):
    p = tmp_path / "guardrails.yaml"
    p.write_bytes(VALID.encode("utf-8").replace(b"customers", b"customers\xff"))
    monkeypatch.setenv(g.GUARDRAILS_ENV, str(p))
    with pytest.raises(GuardrailError, match="UTF-8"):
        g.load_range("1234567890", GoalKind.INSTALL)


def test_nan_days_refuses(rng):
    with pytest.raises(GuardrailError):
        g.check_target_change(rng, 1_000_000, 1_100_000, float("nan"))


def test_digitless_ids_refuse(tmp_path, monkeypatch):
    use(tmp_path, monkeypatch, VALID.replace('"1234567890"', "abc"))
    with pytest.raises(GuardrailError):
        g.load_range("", GoalKind.INSTALL)
    use(tmp_path, monkeypatch)
    with pytest.raises(GuardrailError):
        g.load_range("1234567890", GoalKind.INSTALL, "abc")
    with pytest.raises(GuardrailError):
        g.load_range("", GoalKind.INSTALL)
