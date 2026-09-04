"""Search results get provider origin tags for injection defense (#688).

Each provider (brave, tavily, duckduckgo) must populate the SearchResult.source
field with its provider name so that downstream consumers can distinguish
untrusted web content from trusted system/tool output.
"""

from __future__ import annotations

import pytest

from agentos.search.types import SearchResult
from agentos.tools.builtin.web import _search_payload


class TestSearchResultSourceField:
    """SearchResult.source is populated by each provider."""

    def test_brave_default_source_empty_when_not_set(self) -> None:
        """Legacy SearchResult without source should have empty string."""
        result = SearchResult(title="T", url="https://x.com", snippet="s")
        assert result.source == ""

    def test_source_is_empty_string_payload_default(self) -> None:
        """Search payload serializes missing source as empty string."""
        results = [SearchResult(title="T", url="https://x.com", snippet="s")]
        payload = _search_payload("q", "duckduckgo", results)
        assert payload["results"][0]["source"] == ""

    def test_source_in_payload(self) -> None:
        """Search payload carries the source field."""
        results = [
            SearchResult(title="T", url="https://x.com", snippet="s", source="brave"),
        ]
        payload = _search_payload("q", "brave", results)
        assert payload["results"][0]["source"] == "brave"

    def test_source_in_payload_isolation(self) -> None:
        """Each result carries its own source tag."""
        results = [
            SearchResult(title="A", url="https://a.com", snippet="a", source="brave"),
            SearchResult(title="B", url="https://b.com", snippet="b", source="tavily"),
            SearchResult(title="C", url="https://c.com", snippet="c", source="duckduckgo"),
        ]
        payload = _search_payload("q", "brave", results)
        sources = {r["source"] for r in payload["results"]}
        assert sources == {"brave", "tavily", "duckduckgo"}

    def test_error_payload_no_source(self) -> None:
        """Error payloads should not include source on results."""
        from agentos.tools.builtin.web import _search_error_payload

        exc = ValueError("test error")
        payload = _search_error_payload("q", "brave", exc)
        assert payload["results"] == []


class TestSearchResultProviderTags:
    """End-to-end provider source tagging (no network)."""

    @pytest.mark.asyncio
    async def test_brave_search_sets_source(self, monkeypatch) -> None:
        """Brave provider sets source='brave'."""
        from unittest.mock import AsyncMock, MagicMock

        from agentos.search.providers.brave import BraveSearchProvider

        provider = BraveSearchProvider(api_key="test-key")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "web": {"results": [{"title": "T", "url": "https://x.com", "description": "d"}]}
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr("httpx.AsyncClient.__aenter__", AsyncMock(return_value=mock_client))

        results = await provider.search("hello", max_results=1)
        assert results[0].source == "brave"

    @pytest.mark.asyncio
    async def test_tavily_search_sets_source(self, monkeypatch) -> None:
        """Tavily provider sets source='tavily'."""
        from unittest.mock import AsyncMock, MagicMock

        from agentos.search.providers.tavily import TavilySearchProvider

        provider = TavilySearchProvider(api_key="test-key")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"title": "T", "url": "https://x.com", "content": "d"}]
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        monkeypatch.setattr("httpx.AsyncClient.__aenter__", AsyncMock(return_value=mock_client))

        results = await provider.search("hello", max_results=1)
        assert results[0].source == "tavily"

    def test_search_payload_includes_source(self) -> None:
        """Search payload built from SearchResult includes source."""
        results = [SearchResult(title="T", url="https://x.com", snippet="s", source="tavily")]
        payload = _search_payload("q", "tavily", results)
        assert payload["results"][0]["source"] == "tavily"

    def test_search_payload_defaults_source_to_empty(self) -> None:
        """SearchResult without source gets empty string in payload."""
        results = [SearchResult(title="T", url="https://x.com", snippet="s")]
        payload = _search_payload("q", "duckduckgo", results)
        assert payload["results"][0]["source"] == ""
