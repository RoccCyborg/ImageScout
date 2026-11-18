"""
image_scout.py — Unified CLI for Image/File Footprinting, Web Crawling, Visual Matching & Reverse Search.

Subcommands:
  footprint <file>               Compute SHA-256/MD5, pHash/dHash/aHash, colors, and EXIF/GPS metadata.
  crawl <url> [url2 ...]         Crawl websites (HTTP or Headless Chromium) and index images into SQLite.
  match <query_image>            Search the crawled SQLite index for exact or visually similar images.
  reverse <image_path_or_url>    Generate reverse-search links, run web footprint lookup, or upload via browser.
  stats                          Show statistics for the crawled SQLite image index.
"""

from __future__ import annotations

import argparse
import json
import sys

from crawler import (
    CrawlConfig,
    ImageIndexDB,
    WebImageCrawler,
    search_index_by_hash,
    search_index_by_image,
)
from footprint import analyze_footprint
from reverse_search import browser_reverse_search_local_image, generate_reverse_report
from web_search import search_web


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="image_scout",
        description="Web crawler, perceptual image matcher, and file footprinting toolkit.",
    )
    parser.add_argument("--db", default="image_index.db", help="Path to SQLite index database (default: image_index.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fp = sub.add_parser("footprint", help="Generate cryptographic, perceptual, and EXIF footprint of a file")
    p_fp.add_argument("file", help="Path to image or any file")

    p_crawl = sub.add_parser("crawl", help="Crawl web pages and index discovered images")
    p_crawl.add_argument("urls", nargs="+", help="Seed URL(s) to crawl")
    p_crawl.add_argument("--depth", type=int, default=2, help="Max crawl depth (default: 2)")
    p_crawl.add_argument("--max-pages", type=int, default=30, help="Max pages to crawl (default: 30)")
    p_crawl.add_argument("--cross-domain", action="store_true", help="Follow links outside seed domain(s)")
    p_crawl.add_argument("--js", action="store_true", help="Use Headless Chromium (Selenium) for JS-rendered sites")
    p_crawl.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt restrictions")
    p_crawl.add_argument("--delay", type=float, default=0.5, help="Delay between page fetches in seconds")
    p_crawl.add_argument("--allow-private-network", action="store_true", help="Allow private/local IP targets (trusted local testing only)")

    p_match = sub.add_parser("match", help="Search crawled index for matching or visually similar images")
    p_match.add_argument("query", help="Path to query image (or --hash with SHA-256)")
    p_match.add_argument("--hash", action="store_true", help="Treat query as a SHA-256 hex hash instead of an image path")
    p_match.add_argument("--threshold", type=int, default=12, help="Max Hamming distance for visual match (0-64, default: 12)")
    p_match.add_argument("--limit", type=int, default=20, help="Max results to return")

    p_rev = sub.add_parser("reverse", help="Reverse image search & internet footprint lookup")
    p_rev.add_argument("target", help="Local file path or public image URL")
    p_rev.add_argument("--web-footprint", action="store_true", help="Query DuckDuckGo for file hash/EXIF footprint")
    p_rev.add_argument("--browser-upload", choices=["bing", "tineye"], help="Upload local image via Headless Chromium")

    p_web = sub.add_parser("web-search", help="Search public web results through Brave Search API")
    p_web.add_argument("query", help="Search query")
    p_web.add_argument("--count", type=int, default=10, help="Number of results (1-20)")

    sub.add_parser("stats", help="Show SQLite image index statistics")

    args = parser.parse_args(argv)

    if args.command == "footprint":
        print(json.dumps(analyze_footprint(args.file), indent=2))
        return 0

    if args.command == "crawl":
        cfg = CrawlConfig(
            max_pages=args.max_pages,
            max_depth=args.depth,
            same_domain=not args.cross_domain,
            use_browser=args.js,
            respect_robots=not args.ignore_robots,
            delay_seconds=args.delay,
            allow_private_networks=args.allow_private_network,
        )
        crawler = WebImageCrawler(db_path=args.db, config=cfg)
        try:
            summary = crawler.crawl(args.urls)
        finally:
            crawler.close()
        print(json.dumps(summary, indent=2))
        return 0

    if args.command == "match":
        if args.hash:
            hits = search_index_by_hash(args.db, args.query)
            print(json.dumps({"total_matches": len(hits), "matches": hits[: args.limit]}, indent=2))
        else:
            res = search_index_by_image(args.db, args.query, max_distance=args.threshold, limit=args.limit)
            print(json.dumps(res, indent=2))
        return 0

    if args.command == "reverse":
        report = generate_reverse_report(args.target, run_web_footprint=args.web_footprint)
        if args.browser_upload and not args.target.startswith(("http://", "https://")):
            report["browser_reverse_search"] = browser_reverse_search_local_image(
                args.target, engine=args.browser_upload
            )
        print(json.dumps(report, indent=2))
        return 0

    if args.command == "web-search":
        print(json.dumps(search_web(args.query, count=args.count), indent=2))
        return 0

    if args.command == "stats":
        db = ImageIndexDB(args.db)
        try:
            print(json.dumps(db.get_stats(), indent=2))
        finally:
            db.close()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
