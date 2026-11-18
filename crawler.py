"""
crawler.py — Extensive Web Crawler, Headless Browser Fetcher & Perceptual Image Indexer.

Features:
- Dual fetchers: fast HTTP (httpx/requests + BeautifulSoup) and Headless Chromium (Selenium)
  with auto-scrolling for JavaScript-rendered / lazy-loaded images.
- Extracts <img>, srcset, <picture>, OpenGraph/Twitter meta images, CSS background-image URLs,
  and direct image links.
- Respects robots.txt (configurable), rate limiting, max depth, and max pages.
- Indexes every image into SQLite with SHA-256, pHash, dHash, aHash, dimensions, and source URL.
- Supports searching the local index by query image (visual similarity) or file hash (footprint).
"""

from __future__ import annotations

import io
import re
import sqlite3
import time
import ipaddress
import socket
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from PIL import Image

from footprint import (
    analyze_footprint,
    compute_ahash,
    compute_dhash,
    compute_phash,
    hamming_distance,
    similarity_percent,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
CSS_URL_RE = re.compile(r"url\(['\"]?(.*?)['\"]?\)")


@dataclass
class CrawlConfig:
    max_pages: int = 30
    max_depth: int = 2
    same_domain: bool = True
    use_browser: bool = False
    respect_robots: bool = True
    delay_seconds: float = 0.5
    timeout_seconds: float = 15.0
    max_image_bytes: int = 15 * 1024 * 1024
    allow_private_networks: bool = False
    max_redirects: int = 5
    user_agent: str = "ImageScoutCrawler/1.0 (+https://example.local/bot)"


class ImageIndexDB:
    """SQLite store for crawled pages and image perceptual/cryptographic footprints."""

    def __init__(self, db_path: str | Path = "image_index.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                title TEXT,
                crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_url TEXT NOT NULL,
                page_url TEXT NOT NULL,
                alt_text TEXT,
                mime_type TEXT,
                width INTEGER,
                height INTEGER,
                size_bytes INTEGER,
                sha256 TEXT,
                phash TEXT,
                dhash TEXT,
                ahash TEXT,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(image_url, page_url)
            );

            CREATE INDEX IF NOT EXISTS idx_images_sha256 ON images(sha256);
            CREATE INDEX IF NOT EXISTS idx_images_phash ON images(phash);
            """
        )
        self.conn.commit()

    def upsert_page(self, url: str, title: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pages (url, title) VALUES (?, ?)",
            (url, title[:500]),
        )
        self.conn.commit()

    def upsert_image(self, record: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO images (
                image_url, page_url, alt_text, mime_type, width, height,
                size_bytes, sha256, phash, dhash, ahash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["image_url"],
                record["page_url"],
                record.get("alt_text", "")[:500],
                record.get("mime_type", ""),
                record.get("width", 0),
                record.get("height", 0),
                record.get("size_bytes", 0),
                record.get("sha256", ""),
                record.get("phash", ""),
                record.get("dhash", ""),
                record.get("ahash", ""),
            ),
        )
        self.conn.commit()

    def get_stats(self) -> dict[str, int]:
        pages = self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        images = self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        unique_hashes = self.conn.execute(
            "SELECT COUNT(DISTINCT sha256) FROM images WHERE sha256 != ''"
        ).fetchone()[0]
        return {
            "pages_crawled": pages,
            "images_indexed": images,
            "unique_image_files": unique_hashes,
        }

    def close(self) -> None:
        self.conn.close()


def _parse_srcset(srcset: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for part in srcset.split(","):
        tokens = part.strip().split()
        if tokens:
            urls.append(urljoin(base_url, tokens[0]))
    return urls


def extract_links_and_images(html: str, base_url: str) -> tuple[str, list[str], list[tuple[str, str]]]:
    """
    Parse HTML and return:
    - page_title
    - list of discovered page URLs
    - list of (image_url, alt_text) tuples
    """
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    page_links: list[str] = []
    images: dict[str, str] = {}

    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            val = img.get(attr)
            if val and not str(val).startswith("data:"):
                images.setdefault(urljoin(base_url, str(val)), alt)
        if img.get("srcset"):
            for u in _parse_srcset(str(img["srcset"]), base_url):
                images.setdefault(u, alt)

    for source in soup.find_all("source"):
        if source.get("srcset"):
            for u in _parse_srcset(str(source["srcset"]), base_url):
                images.setdefault(u, "")

    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
            content = meta.get("content")
            if content:
                images.setdefault(urljoin(base_url, str(content)), prop)

    for tag in soup.find_all(style=True):
        style_str = str(tag["style"])
        for match in CSS_URL_RE.findall(style_str):
            if match and not match.startswith("data:"):
                images.setdefault(urljoin(base_url, match), "")

    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        ext = Path(parsed.path).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            images.setdefault(abs_url, (a.get_text() or "").strip())
        else:
            page_links.append(abs_url.split("#")[0])

    return title, list(dict.fromkeys(page_links)), list(images.items())


class WebImageCrawler:
    """Crawls web pages (HTTP or Headless Chromium) and indexes images into SQLite."""

    def __init__(self, db_path: str | Path = "image_index.db", config: CrawlConfig | None = None) -> None:
        self.config = config or CrawlConfig()
        self.db = ImageIndexDB(db_path)
        self.client = httpx.Client(
            headers={"User-Agent": self.config.user_agent},
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
        )
        self._robots_cache: dict[str, RobotFileParser | None] = {}
        self._driver = None

    def _get_driver(self):
        if self._driver is None:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options

            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument(f"--user-agent={self.config.user_agent}")
            self._driver = webdriver.Chrome(options=opts)
            self._driver.set_page_load_timeout(int(self.config.timeout_seconds))
        return self._driver

    def _validate_target(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"Unsupported or unsafe URL: {url}")
        if self.config.allow_private_networks:
            return
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise ValueError(f"Unable to resolve host: {parsed.hostname}") from exc
        if not addresses:
            raise ValueError(f"Host has no resolved addresses: {parsed.hostname}")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValueError(f"Blocked non-public network target: {parsed.hostname} ({ip})")

    def _safe_get(self, url: str) -> httpx.Response:
        """GET a public HTTP(S) URL and revalidate every redirect destination."""
        current = url
        for _ in range(self.config.max_redirects + 1):
            self._validate_target(current)
            response = self.client.get(current, follow_redirects=False)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(str(response.url), location)
        raise ValueError(f"Too many redirects while fetching: {url}")

    def _can_fetch(self, url: str) -> bool:
        if not self.config.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots_cache:
            rp = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            try:
                resp = self._safe_get(robots_url)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    self._robots_cache[origin] = rp
                else:
                    self._robots_cache[origin] = None
            except Exception:
                self._robots_cache[origin] = None

        cached = self._robots_cache[origin]
        return True if cached is None else cached.can_fetch(self.config.user_agent, url)

    def _fetch_html(self, url: str) -> str | None:
        if self.config.use_browser:
            driver = self._get_driver()
            driver.get(url)
            time.sleep(1.2)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(0.6)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.8)
            return driver.page_source

        resp = self._safe_get(url)
        content_type = resp.headers.get("content-type", "").lower()
        if resp.status_code == 200 and ("html" in content_type or "xml" in content_type or not content_type):
            return resp.text
        return None

    def _download_and_index_image(self, image_url: str, page_url: str, alt_text: str) -> bool:
        try:
            resp = self._safe_get(image_url)
            if resp.status_code != 200:
                return False
            data = resp.content
            if not data or len(data) > self.config.max_image_bytes:
                return False

            import hashlib

            sha256 = hashlib.sha256(data).hexdigest()
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                if img.width < 16 or img.height < 16:
                    return False
                phash = compute_phash(img)
                dhash = compute_dhash(img)
                ahash = compute_ahash(img)
                fmt = img.format or ""
                width, height = img.width, img.height

            self.db.upsert_image(
                {
                    "image_url": image_url,
                    "page_url": page_url,
                    "alt_text": alt_text,
                    "mime_type": f"image/{fmt.lower()}" if fmt else resp.headers.get("content-type", ""),
                    "width": width,
                    "height": height,
                    "size_bytes": len(data),
                    "sha256": sha256,
                    "phash": phash,
                    "dhash": dhash,
                    "ahash": ahash,
                }
            )
            return True
        except Exception:
            return False

    def crawl(self, seed_urls: list[str]) -> dict[str, Any]:
        """Run BFS crawl starting from seed_urls."""
        seed_domains = {urlparse(u).netloc for u in seed_urls}
        queue: deque[tuple[str, int]] = deque((u, 0) for u in seed_urls)
        visited: set[str] = set()
        seen_images: set[str] = set()

        pages_crawled = 0
        images_indexed = 0
        errors: list[str] = []

        while queue and pages_crawled < self.config.max_pages:
            url, depth = queue.popleft()
            if url in visited or depth > self.config.max_depth:
                continue
            if self.config.same_domain and urlparse(url).netloc not in seed_domains:
                continue
            if not self._can_fetch(url):
                continue

            visited.add(url)
            try:
                html = self._fetch_html(url)
                if html is None:
                    continue
                pages_crawled += 1
                title, next_links, found_images = extract_links_and_images(html, url)
                self.db.upsert_page(url, title)

                for img_url, alt in found_images:
                    if img_url not in seen_images:
                        seen_images.add(img_url)
                        if self._download_and_index_image(img_url, url, alt):
                            images_indexed += 1

                if depth < self.config.max_depth:
                    for link in next_links:
                        if link not in visited:
                            queue.append((link, depth + 1))

                if self.config.delay_seconds > 0:
                    time.sleep(self.config.delay_seconds)
            except Exception as exc:
                errors.append(f"{url}: {exc}")

        return {
            "pages_crawled": pages_crawled,
            "images_indexed": images_indexed,
            "errors": errors,
            "db_stats": self.db.get_stats(),
        }

    def close(self) -> None:
        self.client.close()
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
        self.db.close()


def search_index_by_image(
    db_path: str | Path,
    query_image_path: str | Path,
    max_distance: int = 12,
    limit: int = 25,
) -> dict[str, Any]:
    """
    Search the crawled SQLite index for exact or visually similar matches
    to a local query image using SHA-256 + pHash/dHash Hamming distance.
    """
    fp = analyze_footprint(query_image_path)
    if not fp["is_image"] or not fp["perceptual_hashes"]:
        raise ValueError(f"Query file is not a supported image: {query_image_path}")

    q_sha = fp["crypto"]["sha256"]
    q_phash = fp["perceptual_hashes"]["phash"]
    q_dhash = fp["perceptual_hashes"]["dhash"]
    q_ahash = fp["perceptual_hashes"]["ahash"]

    db = ImageIndexDB(db_path)
    try:
        rows = db.conn.execute("SELECT * FROM images").fetchall()
    finally:
        db.close()

    matches: list[dict[str, Any]] = []
    for row in rows:
        exact = row["sha256"] == q_sha
        p_dist = hamming_distance(q_phash, row["phash"] or "")
        d_dist = hamming_distance(q_dhash, row["dhash"] or "")
        a_dist = hamming_distance(q_ahash, row["ahash"] or "")
        best_dist = min(p_dist, d_dist, a_dist)

        if exact or best_dist <= max_distance:
            matches.append(
                {
                    "match_type": "EXACT_FILE" if exact else "VISUAL_SIMILAR",
                    "similarity_percent": 100.0 if exact else similarity_percent(best_dist),
                    "best_distance": best_dist,
                    "phash_distance": p_dist,
                    "dhash_distance": d_dist,
                    "ahash_distance": a_dist,
                    "image_url": row["image_url"],
                    "page_url": row["page_url"],
                    "alt_text": row["alt_text"],
                    "dimensions": f"{row['width']}x{row['height']}",
                    "sha256": row["sha256"],
                }
            )

    matches.sort(key=lambda m: (0 if m["match_type"] == "EXACT_FILE" else 1, m["best_distance"]))
    return {
        "query_footprint": fp,
        "total_matches": len(matches),
        "matches": matches[:limit],
    }


def search_index_by_hash(db_path: str | Path, sha256_hash: str) -> list[dict[str, Any]]:
    """Search the crawled SQLite index for an exact SHA-256 file footprint."""
    db = ImageIndexDB(db_path)
    try:
        rows = db.conn.execute(
            "SELECT * FROM images WHERE LOWER(sha256) = LOWER(?)",
            (sha256_hash.strip(),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()
