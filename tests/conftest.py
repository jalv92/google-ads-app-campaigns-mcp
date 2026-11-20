from unittest.mock import MagicMock

import pytest
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials


class FakeAds:
    """A GoogleAdsClient stand-in: real types, mocked services."""

    def __init__(self):
        self.real = GoogleAdsClient(
            credentials=AnonymousCredentials(),
            developer_token="fake-token",
            use_proto_plus=True,
        )
        self.services: dict[str, MagicMock] = {}
        self.client = MagicMock(spec=GoogleAdsClient)
        self.client.enums = self.real.enums
        self.client.get_type.side_effect = self.real.get_type
        self.client.get_service.side_effect = self._get_service

    def _get_service(self, name, **kwargs):
        return self.services.setdefault(name, MagicMock())


@pytest.fixture(autouse=True)
def _forget_target_commits():
    from ads_app_mcp.tools import manage_campaign

    manage_campaign._target_commits.clear()


@pytest.fixture
def fake_ads(monkeypatch) -> FakeAds:
    fake = FakeAds()
    for attr in ("get_googleads_client", "_get_googleads_client"):
        monkeypatch.setattr(
            f"ads_mcp.utils.{attr}", lambda *a, **k: fake.client
        )
    return fake


@pytest.fixture
def guardrails_file(tmp_path, monkeypatch):
    path = tmp_path / "guardrails.yaml"
    path.write_text(
        "customers:\n"
        '  "1234567890":\n'
        "    defaults:\n"
        "      install: {min: 0.80, max: 1.50, max_step_pct: 20,"
        " min_days_between_changes: 3}\n"
        "      action: {min: 2.00, max: 6.00, max_step_pct: 20,"
        " min_days_between_changes: 3}\n"
    )
    monkeypatch.setenv("GOOGLE_ADS_APP_MCP_GUARDRAILS", str(path))
    return path
