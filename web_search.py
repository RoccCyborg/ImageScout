"""Search-provider adapters for public-web discovery.

Brave Search is enabled with BRAVE_SEARCH_API_KEY. This module deliberately
uses an official JSON API rather than scraping search-result HTML.
"""
from __future__ import annotations
import os
from typing import Any
import httpx

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def search_web(query: str, *, count: int = 10, timeout: float = 20.0) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("Search query cannot be empty")
    key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not key:
        return {
            "provider": "brave",
            "configured": False,
            "message": "Set BRAVE_SEARCH_API_KEY to enable live web-result retrieval.",
            "query": query,
            "results": [],
        }
    count = max(1, min(int(count), 20))
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(BRAVE_ENDPOINT, params={"q": query, "count": count}, headers=headers)
        response.raise_for_status()
        payload = response.json()
    results = []
    for item in payload.get("web", {}).get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "description": item.get("description", ""),
            "age": item.get("age"),
            "language": item.get("language"),
        })
    return {"provider": "brave", "configured": True, "query": query, "results": results}
