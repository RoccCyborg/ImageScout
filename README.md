# ImageScout 2.0 — Web Crawler, Image Matcher & Digital Footprint Toolkit

ImageScout is a **local-first research tool** for analyzing files, crawling selected public websites, indexing images, finding exact/visually similar matches in its own index, and launching external reverse-image searches.

It is not a complete index of the internet. Internet-wide discovery requires external search indexes/providers; crawling expands the local index over time.

## Capabilities

- File footprint: MD5, SHA-1, SHA-256, SHA-512, byte size and MIME detection.
- Image footprint: pHash, dHash, aHash, dimensions, dominant colors, EXIF and available GPS metadata.
- Crawler: breadth-first crawling, robots.txt checks, rate limiting, same-domain or cross-domain scope, static HTML and optional Selenium rendering.
- Image extraction: `img`, lazy attributes, `srcset`, `picture/source`, OpenGraph/Twitter cards, inline CSS backgrounds and direct image links.
- Local visual search: compare a query image with images already in the SQLite index.
- External discovery: Brave Search API for text/hash/filename queries, plus manual reverse-image provider handoff to Google Lens, Bing Visual Search, Yandex, TinEye and SauceNAO.
- FastAPI service: upload/footprint, indexed match, hash lookup, web search, crawl control and index statistics.

## Important search boundaries

1. **Exact match** means identical bytes (SHA-256).
2. **Perceptual match** means similar low-frequency image structure (pHash/dHash/aHash). It is useful for resizing/recompression but is not a guarantee for heavy crops, collages, rotations or visually related images.
3. **Internet discovery** uses external search providers and crawling. A local crawler only knows pages it has actually visited. Search engines do not expose unrestricted global image indexes for arbitrary local queries through a free universal API.
4. The reverse-search links open provider sites. Automated upload/scraping is intentionally not treated as a reliable core API because provider interfaces, authentication, rate limits and terms can change.

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For JavaScript-rendered crawling, install Google Chrome or Chromium. Selenium Manager generally resolves the matching driver automatically; verify it works on the host before running `--js`.

## CLI

```powershell
# File/image footprint
python image_scout.py footprint .\photo.jpg

# Crawl a site (respect robots.txt; same domain by default)
python image_scout.py --db .\data\image_index.db crawl https://example.org --depth 2 --max-pages 50 --delay 1

# JavaScript-rendered pages
python image_scout.py --db .\data\image_index.db crawl https://example.org --js --depth 1 --max-pages 20

# Trusted local test target only (explicitly enables private IPs)
python image_scout.py crawl http://127.0.0.1:9000 --allow-private-network

# Search the local crawl index
python image_scout.py --db .\data\image_index.db match .\query.jpg --threshold 12 --limit 50

# Exact SHA-256 lookup
python image_scout.py --db .\data\image_index.db match <sha256_hex> --hash

# Build reverse-search links / footprint report
python image_scout.py reverse .\photo.jpg

# Search public web text results (requires a Brave Search API key)
$env:BRAVE_SEARCH_API_KEY = "YOUR_KEY"
python image_scout.py web-search '"filename.jpg" OR "sha256-here"' --count 10

# Index statistics
python image_scout.py --db .\data\image_index.db stats
```

## Run the local web API + dashboard

```powershell
$env:IMAGESCOUT_DB = "$PWD\data\image_index.db"
$env:BRAVE_SEARCH_API_KEY = "YOUR_KEY"  # optional; enables live web search
uvicorn api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` for the dashboard (including the new Web Intelligence tab) and `http://127.0.0.1:8000/docs` for the API reference.

Endpoints:

- `POST /api/footprint` — multipart `file`
- `POST /api/match` — multipart `file`, optional `threshold`, `limit`
- `GET /api/match/hash/{sha256}` — exact hash lookup
- `POST /api/web-search` — JSON `{ "query": "...", "count": 10 }`
- `POST /api/reverse` — multipart `file`; returns footprint, query ideas and provider handoff links
- `POST /api/crawl` — optional local crawl job (disabled by default)
- `GET /api/stats` — index counts

To enable local crawl API: set `IMAGESCOUT_ENABLE_CRAWL_API=1` before starting the server. Keep the service bound to `127.0.0.1`. **Do not expose this API directly to the public internet** without authentication, request throttling, a hardened SSRF-resistant fetch layer, TLS and isolated workers. Treat all user-provided URLs and downloaded files as untrusted.

## Recommended next engineering stages

- Stage 1: API-backed dashboard, provider configuration, crawl job queue and crawl logs.
- Stage 2: PostgreSQL for metadata, object storage for thumbnails, and a vector/ANN index for larger collections.
- Stage 3: crop/rotation-resistant local matching using local features (e.g. ORB) and image embeddings; evaluate precision/recall against a labelled test set.
- Stage 4: additional licensed search-provider adapters, scheduled recrawls, sitemap ingestion, domain allow/deny lists and exportable evidence reports.

## Operational constraints

- The crawler blocks non-public IP destinations by default and validates redirect destinations. Use `--allow-private-network` only for trusted local test targets. DNS rebinding and infrastructure-level egress controls still require deployment-level protection.
- Respect each site's robots.txt, terms, copyright and rate limits. Do not bypass authentication or access controls.
- Do not use the tool to identify private people or infer sensitive attributes from images.
- EXIF GPS may reveal sensitive location data. Review metadata before sharing reports.
- Search-engine result pages and reverse-image providers can change their interfaces and availability.
- The current SQLite scan is suitable for small/medium personal indexes; large-scale indexes need a dedicated similarity-search index and worker architecture.
