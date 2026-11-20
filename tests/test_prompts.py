import asyncio
import re
from importlib.resources import files

from ads_app_mcp.server import build_server

URI = "resource://app-campaign-playbook"


def _playbook() -> str:
    return files("ads_app_mcp").joinpath("playbook.md").read_text("utf-8")


def _section(title: str) -> str:
    match = re.search(
        rf"^## {title}\n(.*?)(?=^## |\Z)", _playbook(), re.M | re.S
    )
    assert match, f"playbook has no '## {title}' section"
    return match.group(1)


def _items(text: str) -> list[str]:
    """Bullets and paragraphs, each with its wrapped lines joined."""
    items: list[str] = []
    new = True
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            new = True
        elif new or re.match(r"\s*[-*] ", line):
            items.append(line.strip())
            new = False
        else:
            items[-1] += " " + line.strip()
    return items


def _render(**arguments) -> str:
    result = asyncio.run(
        build_server().render_prompt("daily_review", arguments)
    )
    return "\n".join(m.content.text for m in result.messages)


def test_playbook_resource_is_listed():
    uris = [str(r.uri) for r in asyncio.run(build_server().list_resources())]
    assert URI in uris


def test_playbook_resource_returns_the_playbook():
    result = asyncio.run(build_server().read_resource(URI))
    assert result.contents[0].content == _playbook()


def test_daily_review_prompt_is_listed():
    names = [p.name for p in asyncio.run(build_server().list_prompts())]
    assert "daily_review" in names


def test_daily_review_mentions_customer_and_playbook():
    text = _render(customer_id="1234567890")
    assert "1234567890" in text
    assert URI in text
    assert "every ENABLED App campaign" in text


def test_daily_review_names_the_campaign_when_given():
    text = _render(customer_id="1234567890", campaign_id="111")
    assert "campaign 111" in text
    assert "every ENABLED App campaign" not in text


def test_daily_review_says_nobody_is_watching_when_unattended(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_APP_MCP_UNATTENDED", "1")
    text = _render(customer_id="1234567890")
    assert "This run is unattended: nobody is watching." in text
    assert "A user is in the conversation." not in text


def test_daily_review_says_a_user_is_present_when_attended(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_APP_MCP_UNATTENDED", raising=False)
    text = _render(customer_id="1234567890")
    assert "A user is in the conversation." in text
    assert "This run is unattended" not in text


def test_every_number_rule_has_source_or_default():
    items = _items(_section("Decision rules"))
    assert items
    for item in items:
        if re.search(r"\d", item):
            assert "http" in item or "adjustable default" in item.lower(), item


def test_raise_rule_requires_cost_close_to_target():
    items = _items(_section("Decision rules"))
    raise_rule = next(i for i in items if i.startswith("- Raise one step"))
    assert "at least 90 % of the target" in raise_rule
    assert "closely tracking" in raise_rule
    assert "adjustable default" in raise_rule


def test_playbook_recipes_only_select():
    blocks = re.findall(r"^```sql\n(.*?)^```", _playbook(), re.M | re.S)
    assert blocks
    for block in blocks:
        assert block.lstrip().startswith("SELECT"), block
