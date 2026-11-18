"""
reverse_search.py — Internet Reverse Image Search & File Footprint Lookup.

Provides:
1. Direct reverse-image search URL builders (Google Lens, Bing Visual Search,
   Yandex Images, TinEye, SauceNAO) for any public image URL.
2. Automated headless Chromium (Selenium) reverse-image upload & result scraping
   for local image files (Bing Visual Search / TinEye / Google Images).
3. Web footprint search queries (SHA-256, MD5, filename, EXIF camera/GPS)
   with optional live DuckDuckGo HTML scraping.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from footprint import analyze_footprint


def build_reverse_search_urls(image_url: str) -> dict[str, str]:
    """Generate direct reverse image search links for a public image URL."""
    encoded = quote_plus(image_url)
    return {
        "google_lens": f"https://lens.google.com/uploadbyurl?url={encoded}",
        "bing_visual_search": f"https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIIRP&sbisrc=UrlPaste&q=imgurl:{encoded}",
        "yandex_images": f"https://yandex.com/images/search?rpt=imageview&url={encoded}",
        "tineye": f"https://tineye.com/search?url={encoded}",
        "saucenao": f"https://saucenao.com/search.php?url={encoded}",
    }


def build_footprint_search_queries(footprint: dict[str, Any]) -> list[str]:
    """Build targeted web search queries from a file's cryptographic & metadata footprint."""
    crypto = footprint.get("crypto", {})
    queries: list[str] = []

    if crypto.get("sha256"):
        queries.append(f'"{crypto["sha256"]}"')
    if crypto.get("md5"):
        queries.append(f'"{crypto["md5"]}"')
    if footprint.get("file_name"):
        queries.append(f'"{footprint["file_name"]}"')

    meta = footprint.get("image_metadata") or {}
    exif = meta.get("exif") or {}
    model = exif.get("Model")
    dt = exif.get("DateTimeOriginal") or exif.get("DateTime")
    if model and dt:
        queries.append(f'"{model}" "{dt}"')

    return queries


def search_web_footprint_ddg(query: str, max_results: int = 10) -> list[dict[str, str]]:
    """
    Query DuckDuckGo's lightweight HTML endpoint for a file hash or footprint string.
    Returns matching titles, URLs, and snippets.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
    results: list[dict[str, str]] = []

    with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True) as client:
        resp = client.post(url, data={"q": query})
        if resp.status_code != 200:
            return results
        soup = BeautifulSoup(resp.text, "lxml")
        for item in soup.select(".result")[:max_results]:
            link = item.select_one(".result__a")
            snippet = item.select_one(".result__snippet")
            if link and link.get("href"):
                results.append(
                    {
                        "title": link.get_text(strip=True),
                        "url": str(link["href"]),
                        "snippet": snippet.get_text(strip=True) if snippet else "",
                    }
                )
    return results


def browser_reverse_search_local_image(
    image_path: str | Path,
    engine: str = "bing",
    headless: bool = True,
    wait_seconds: float = 5.0,
) -> dict[str, Any]:
    """
    Use Selenium + Chromium to upload a local image to a reverse image search engine
    (currently supports 'bing' and 'tineye') and extract matching page URLs.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By

    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(25)
    matches: list[dict[str, str]] = []

    try:
        if engine.lower() == "bing":
            driver.get("https://www.bing.com/visualsearch")
            time.sleep(2.0)
            file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
            if not file_inputs:
                return {"engine": "bing", "status": "upload_input_not_found", "matches": []}
            file_inputs[0].send_keys(str(path))
            time.sleep(wait_seconds)
            for a in driver.find_elements(By.CSS_SELECTOR, "a[href^='http']")[:30]:
                href = a.get_attribute("href") or ""
                text = (a.text or "").strip()
                if href and "bing.com" not in href and "microsoft.com" not in href:
                    matches.append({"title": text or href, "url": href})

        elif engine.lower() == "tineye":
            driver.get("https://tineye.com/")
            time.sleep(2.0)
            file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
            if not file_inputs:
                return {"engine": "tineye", "status": "upload_input_not_found", "matches": []}
            file_inputs[0].send_keys(str(path))
            time.sleep(wait_seconds)
            for a in driver.find_elements(By.CSS_SELECTOR, ".match a[href^='http']")[:30]:
                href = a.get_attribute("href") or ""
                if href and "tineye.com" not in href:
                    matches.append({"title": a.text.strip() or href, "url": href})
        else:
            raise ValueError(f"Unsupported browser upload engine: {engine}. Choose 'bing' or 'tineye'.")

        seen: set[str] = set()
        unique_matches = []
        for m in matches:
            if m["url"] not in seen:
                seen.add(m["url"])
                unique_matches.append(m)

        return {
            "engine": engine,
            "status": "ok",
            "result_page_url": driver.current_url,
            "matches": unique_matches,
        }
    finally:
        driver.quit()


def generate_reverse_report(target: str, run_web_footprint: bool = False) -> dict[str, Any]:
    """
    Given a local file path or an http(s) image URL, produce reverse-search links
    and (for local files) full footprint + optional web hash lookup.
    """
    if target.startswith(("http://", "https://")):
        return {
            "target": target,
            "type": "url",
            "reverse_search_urls": build_reverse_search_urls(target),
        }

    fp = analyze_footprint(target)
    queries = build_footprint_search_queries(fp)
    web_hits: dict[str, list[dict[str, str]]] = {}
    if run_web_footprint:
        for q in queries[:2]:
            try:
                web_hits[q] = search_web_footprint_ddg(q)
            except Exception:
                web_hits[q] = []

    return {
        "target": str(Path(target).resolve()),
        "type": "local_file",
        "footprint": fp,
        "footprint_search_queries": queries,
        "web_footprint_hits": web_hits,
    }
