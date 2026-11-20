"""Target-cost guardrails and the unattended commit gate.

The user owns a YAML file that says how far an automated agent may move a
campaign's target cost. Every failure to read or match it refuses the change.
Nothing here calls the Google Ads API.
"""

import math
import os
import re
from dataclasses import dataclass
from enum import Enum

import ads_mcp.utils as utils
import yaml
from fastmcp.exceptions import ToolError

GUARDRAILS_ENV = "GOOGLE_ADS_APP_MCP_GUARDRAILS"
UNATTENDED_ENV = "GOOGLE_ADS_APP_MCP_UNATTENDED"
UNATTENDED_COMMIT_ALLOWED = frozenset({"set_target_cost", "update_app_ad"})
MAX_COOLDOWN_DAYS = 28
_KEYS = ("min", "max", "max_step_pct", "min_days_between_changes")


class GoalKind(str, Enum):
    INSTALL = "install"
    ACTION = "action"


class GuardrailError(Exception):
    """The guardrails file is unusable or the change breaks it."""


@dataclass(frozen=True)
class TargetRange:
    min_micros: int
    max_micros: int
    max_step_pct: float
    min_days_between_changes: int


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses a key written twice in one mapping (plain
    PyYAML silently keeps the last one)."""

    def construct_mapping(self, node, deep=False):
        seen = []
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.append(key)
        return super().construct_mapping(node, deep=deep)


def _digits(key) -> str:
    out = re.sub(r"\D", "", utils.extract_id(str(key)))
    if not out:
        raise GuardrailError(f"ID {key!r} has no digits")
    return out


def _number(raw, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise GuardrailError(f"{name} must be a number, got {raw!r}")
    if not math.isfinite(raw):
        raise GuardrailError(f"{name} must be finite, got {raw!r}")
    return float(raw)


def _parse_range(raw, where: str) -> TargetRange:
    if not isinstance(raw, dict):
        raise GuardrailError(f"{where}: expected a mapping of range keys")
    unknown = sorted(str(k) for k in raw if k not in _KEYS)
    if unknown:
        raise GuardrailError(f"{where}: unknown keys {unknown}")
    missing = [k for k in _KEYS if k not in raw]
    if missing:
        raise GuardrailError(f"{where}: missing keys {missing}")
    lo = _number(raw["min"], f"{where}.min")
    hi = _number(raw["max"], f"{where}.max")
    step = _number(raw["max_step_pct"], f"{where}.max_step_pct")
    days = _number(
        raw["min_days_between_changes"], f"{where}.min_days_between_changes"
    )
    if lo <= 0:
        raise GuardrailError(f"{where}.min must be > 0")
    if hi < lo:
        raise GuardrailError(f"{where}.max must be >= min")
    if not 0 < step <= 100:
        raise GuardrailError(f"{where}.max_step_pct must be in (0, 100]")
    if days != int(days) or not 0 <= days <= MAX_COOLDOWN_DAYS:
        raise GuardrailError(
            f"{where}.min_days_between_changes must be a whole number "
            f"0-{MAX_COOLDOWN_DAYS}"
        )
    return TargetRange(
        round(lo * 1_000_000), round(hi * 1_000_000), step, int(days)
    )


def _match(mapping: dict, want: str, what: str) -> list:
    """Values of the keys whose digits are `want`. Every key is read, so a
    digitless key anywhere refuses, and two keys for one ID refuse."""
    hits = [v for k, v in mapping.items() if _digits(k) == want]
    if len(hits) > 1:
        raise GuardrailError(
            f"{what} {want} appears more than once in the guardrails file"
        )
    return hits


def load_range(
    customer_id: str | int,
    goal_kind: GoalKind,
    campaign_id: str | int | None = None,
) -> TargetRange:
    path = os.environ.get(GUARDRAILS_ENV)
    if not path:
        raise GuardrailError(
            f"{GUARDRAILS_ENV} is not set; refusing to change a target cost"
        )
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=_UniqueKeyLoader)
    except UnicodeDecodeError as e:
        raise GuardrailError(
            f"guardrails file is not valid UTF-8 text: {e}"
        ) from e
    except (OSError, yaml.YAMLError) as e:
        raise GuardrailError(f"cannot read guardrails file: {e}") from e
    customers = data.get("customers") if isinstance(data, dict) else None
    if not isinstance(customers, dict):
        raise GuardrailError("guardrails file has no 'customers' mapping")
    want = _digits(customer_id)
    # Every ID key must have digits, not only the ones a lookup reaches.
    for c in customers.values():
        if isinstance(c, dict) and isinstance(c.get("campaigns"), dict):
            for k in c["campaigns"]:
                _digits(k)
    hits = _match(customers, want, "customer")
    cust = hits[0] if hits else None
    if not isinstance(cust, dict):
        raise GuardrailError(f"customer {customer_id} not in guardrails file")
    unknown = sorted(str(k) for k in cust if k not in ("defaults", "campaigns"))
    if unknown:
        raise GuardrailError(f"customer {customer_id}: unknown keys {unknown}")
    campaigns = cust.get("campaigns", {})
    if campaigns is None:
        campaigns = {}
    if not isinstance(campaigns, dict):
        raise GuardrailError("'campaigns' must be a mapping of campaign IDs")
    if "defaults" in cust and not isinstance(cust["defaults"], dict):
        raise GuardrailError("'defaults' must be a mapping")
    defaults = cust.get("defaults") or {}
    unknown = sorted(
        str(k) for k in defaults if k not in {g.value for g in GoalKind}
    )
    if unknown:
        raise GuardrailError(f"defaults: unknown goal kinds {unknown}")
    if campaign_id is not None:
        hits = _match(campaigns, _digits(campaign_id), "campaign")
        if hits:
            return _parse_range(hits[0], f"campaign {campaign_id}")
    raw = defaults.get(goal_kind.value)
    if raw is None:
        raise GuardrailError(
            f"no default range for goal '{goal_kind.value}' "
            f"on customer {customer_id}"
        )
    return _parse_range(raw, f"defaults.{goal_kind.value}")


def _check_bounds(rng: TargetRange, micros: int) -> None:
    if not rng.min_micros <= micros <= rng.max_micros:
        raise GuardrailError(
            f"{micros / 1e6:.2f} is outside the range the user allows "
            f"({rng.min_micros / 1e6:.2f}-{rng.max_micros / 1e6:.2f})"
        )


def check_initial_target(rng: TargetRange, target_micros: int) -> None:
    _check_bounds(rng, target_micros)


def check_target_change(
    rng: TargetRange,
    current_micros: int,
    new_micros: int,
    days_since_last_change: float | None,
) -> None:
    if days_since_last_change is not None and not math.isfinite(
        days_since_last_change
    ):
        raise GuardrailError("days_since_last_change must be finite")
    if new_micros == current_micros:
        raise GuardrailError("new target equals the current target")
    _check_bounds(rng, new_micros)
    if current_micros <= 0:
        raise GuardrailError("current target must be positive")
    step = abs(new_micros - current_micros) * 100 / current_micros
    if step > rng.max_step_pct:
        raise GuardrailError(
            f"change of {step:.1f}% exceeds the {rng.max_step_pct}% limit"
        )
    if (
        days_since_last_change is not None
        and days_since_last_change < rng.min_days_between_changes
    ):
        raise GuardrailError(
            f"last change was {days_since_last_change:.1f} days ago; "
            f"wait {rng.min_days_between_changes} days between changes"
        )


def is_unattended() -> bool:
    """Any value but empty, 0, false or no means unattended: an unknown value
    must not let a scheduled run commit what only a user may approve."""
    return os.environ.get(UNATTENDED_ENV, "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
    }


def ensure_commit_allowed(tool_name: str, validate_only: bool) -> None:
    if (
        is_unattended()
        and not validate_only
        and tool_name not in UNATTENDED_COMMIT_ALLOWED
    ):
        raise ToolError(
            f"{tool_name} cannot commit in an unattended run; only "
            f"{sorted(UNATTENDED_COMMIT_ALLOWED)} may. Use validate_only=True."
        )
