import asyncio
import importlib.util
from pathlib import Path

_PATH = Path(__file__).parent.parent / "scripts" / "test_account_run.py"
_spec = importlib.util.spec_from_file_location("account_run", _PATH)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


class _Runner:
    def __init__(self, events):
        self.events = events

    async def search(self, *args, **kwargs):
        return self.events


def _event(resource_type):
    return {
        "change_event.change_resource_type": resource_type,
        "change_event.resource_change_operation": "UPDATE",
        "change_event.changed_fields": {
            "paths": ["target_cpa.target_cpa_micros"]
        },
    }


def _logged(events):
    return asyncio.run(run.target_change_logged(_Runner(events), "111"))[0]


def test_target_change_logged_needs_a_campaign_row(monkeypatch):
    monkeypatch.setattr(run, "RETRY_SECONDS", 0)
    assert not _logged([_event("AD_GROUP")])
    assert _logged([_event("CAMPAIGN")])


def _real_event(operation, changed_fields):
    """A row as upstream's search tool really returns it (first live run,
    2026-11-20): the field mask is ONE comma-joined string of camelCase
    paths."""
    return {
        "change_event.change_resource_type": "CAMPAIGN",
        "change_event.resource_change_operation": operation,
        "change_event.changed_fields": changed_fields,
    }


def test_target_change_logged_reads_the_live_field_mask_form(monkeypatch):
    monkeypatch.setattr(run, "RETRY_SECONDS", 0)
    assert _logged([_real_event("UPDATE", "targetCpa.targetCpaMicros")])
    assert _logged([_real_event("UPDATE", "status,targetCpa.targetCpaMicros")])
    assert not _logged([_real_event("UPDATE", "status")])
    # the campaign's creation lists the target too, but is not an UPDATE
    assert not _logged(
        [_real_event("CREATE", "name,status,targetCpa.targetCpaMicros")]
    )
