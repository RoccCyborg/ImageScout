"""Local-first FastAPI service for ImageScout.

Run on localhost. Do not expose this development service directly to the public
internet without authentication, rate limiting, TLS, and a hardened crawl worker.
"""
from __future__ import annotations
import os, tempfile
from pathlib import Path
from typing import Any
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import httpx
from pydantic import BaseModel, Field, HttpUrl

from footprint import analyze_footprint
from crawler import CrawlConfig, ImageIndexDB, WebImageCrawler, search_index_by_image, search_index_by_hash
from reverse_search import build_reverse_search_urls, build_footprint_search_queries
from web_search import search_web

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("IMAGESCOUT_DB", str(ROOT / "image_index.db")))
MAX_UPLOAD = int(os.getenv("IMAGESCOUT_MAX_UPLOAD", str(20 * 1024 * 1024)))
app = FastAPI(title="ImageScout API", version="2.0.0", description="Image footprinting, web crawling and indexed visual search")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    count: int = Field(default=10, ge=1, le=20)

class CrawlRequest(BaseModel):
    urls: list[HttpUrl] = Field(min_length=1, max_length=10)
    max_pages: int = Field(default=30, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=5)
    same_domain: bool = True
    javascript: bool = False
    delay_seconds: float = Field(default=1.0, ge=0.5, le=10.0)

@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version, "database": str(DB_PATH), "brave_search_configured": bool(os.getenv("BRAVE_SEARCH_API_KEY"))}

async def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "upload.bin").suffix[:12]
    fd, name = tempfile.mkstemp(prefix="imagescout_", suffix=suffix)
    path = Path(name)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD} bytes")
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path

@app.post("/api/footprint")
async def footprint(file: UploadFile = File(...)):
    path = await save_upload(file)
    try:
        try:
            return analyze_footprint(path)
        except Exception as exc:
            raise HTTPException(400, f"Unable to analyze file: {exc}") from exc
    finally:
        path.unlink(missing_ok=True)

@app.post("/api/match")
async def match(file: UploadFile = File(...), threshold: int = Form(12), limit: int = Form(50)):
    if not 0 <= threshold <= 64 or not 1 <= limit <= 200:
        raise HTTPException(422, "threshold must be 0..64 and limit 1..200")
    path = await save_upload(file)
    try:
        try:
            return search_index_by_image(DB_PATH, path, max_distance=threshold, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)

@app.get("/api/match/hash/{sha256}")
def match_hash(sha256: str):
    if len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256):
        raise HTTPException(422, "Expected a 64-character SHA-256 hex digest")
    hits = search_index_by_hash(DB_PATH, sha256)
    return {"total_matches": len(hits), "matches": hits}

@app.post("/api/web-search")
def web_search_endpoint(request: WebSearchRequest):
    try:
        return search_web(request.query, count=request.count)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"Search provider returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Search provider request failed: {exc}") from exc

@app.post("/api/reverse")
async def reverse(file: UploadFile = File(...)):
    path = await save_upload(file)
    try:
        fp = analyze_footprint(path)
        return {
            "footprint": fp,
            "search_queries": build_footprint_search_queries(fp),
            "reverse_search": "For privacy and reliability, open a provider and upload the file manually.",
            "providers": {
                "Google Lens": "https://lens.google.com/",
                "Bing Visual Search": "https://www.bing.com/visualsearch",
                "Yandex Images": "https://yandex.com/images/",
                "TinEye": "https://tineye.com/",
                "SauceNAO": "https://saucenao.com/",
            },
        }
    finally:
        path.unlink(missing_ok=True)

@app.post("/api/crawl")
def crawl(request: CrawlRequest):
    if os.getenv("IMAGESCOUT_ENABLE_CRAWL_API", "0") != "1":
        raise HTTPException(403, "Crawl API disabled. Set IMAGESCOUT_ENABLE_CRAWL_API=1 for trusted local use.")
    urls = [str(u) for u in request.urls]
    cfg = CrawlConfig(max_pages=request.max_pages, max_depth=request.max_depth,
                      same_domain=request.same_domain, use_browser=request.javascript,
                      respect_robots=True, delay_seconds=request.delay_seconds)
    crawler = WebImageCrawler(db_path=DB_PATH, config=cfg)
    try:
        return crawler.crawl(urls)
    finally:
        crawler.close()

@app.get("/api/stats")
def stats():
    db = ImageIndexDB(DB_PATH)
    try:
        return db.get_stats()
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
def dashboard():
    page = ROOT / "index.html"
    if not page.exists():
        return HTMLResponse("ImageScout dashboard not found", status_code=404)
    return HTMLResponse(page.read_text(encoding="utf-8"))
