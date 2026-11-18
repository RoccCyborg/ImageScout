# ImageScout 2.2 — Local Web Crawler, Image Matcher & Digital Footprint Toolkit

ImageScout is a local-first research tool for analyzing files, crawling selected public websites, indexing images, finding exact/visually similar matches in its own SQLite index, and launching external reverse-image searches.

It is not a complete index of the internet. Internet-wide discovery requires external search indexes/providers; crawling expands the local index over time.

## Current architecture

- Frontend + API: served together by FastAPI on your Windows laptop.
- Database: local SQLite at `data/image_index.db`.
- Temporary/local storage: the database remains on disk between restarts. Delete `data/image_index.db` to reset the index. It is not cloud-backed.
- Web search: optional Brave Search API key.
- Android access: optional LAN mode lets another device on the same trusted Wi-Fi open the laptop-hosted dashboard.

## Windows 11 quick start

1. Install Python 3.11 or newer and Git.
2. Clone this repository or download the repository ZIP and extract it.
3. Open the extracted `ImageScout` folder in File Explorer.
4. Right-click `start_imagescout.ps1` and choose **Run with PowerShell**, or open PowerShell in that folder and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start_imagescout.ps1
```

The script creates a virtual environment, installs dependencies, creates `.env` from `.env.example`, creates the data folder and starts the local server.

Open:
- Dashboard: `http://127.0.0.1:8000`
- API documentation: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/api/health`

Stop the server with Ctrl+C in the PowerShell window.

## Open the dashboard on an Android phone (same Wi-Fi)

1. Connect the laptop and phone to the same trusted private Wi-Fi network.
2. In PowerShell, run:

```powershell
.\start_imagescout.ps1 -LanAccess
```

3. Read the LAN dashboard address printed by the script, for example `http://192.168.1.20:8000`, and open that exact address on the phone.
4. If Windows Firewall asks, allow Python only on **Private networks**. Do not enable Public network access.

LAN mode binds to all laptop network interfaces so the phone can connect. Do not use it on public Wi-Fi, do not configure router port forwarding, and do not expose port 8000 to the public internet. The local API has no user authentication.

## Configure optional public web search

Edit the local `.env` file and set:

```dotenv
BRAVE_SEARCH_API_KEY=your_key_here
```

Restart ImageScout after changing environment settings. Without a key, the dashboard and local crawler still work; live Brave web results remain disabled.

## Features

- File footprint: MD5, SHA-1, SHA-256, SHA-512, byte size and MIME detection.
- Image footprint: pHash, dHash, aHash, dimensions, dominant colors, EXIF and available GPS metadata.
- Crawler: breadth-first crawling, robots.txt checks, rate limiting, same-domain or cross-domain scope, static HTML and optional Selenium rendering through the CLI.
- Image extraction: `img`, lazy attributes, `srcset`, `picture/source`, OpenGraph/Twitter cards, inline CSS backgrounds and direct image links.
- Local visual search: compare a query image with images already in the SQLite index.
- External discovery: Brave Search API for text/hash/filename queries, plus manual reverse-image provider handoff to Google Lens, Bing Visual Search, Yandex, TinEye and SauceNAO.
- FastAPI service: upload/footprint, indexed match, hash lookup, web search, crawl control and index statistics.

## Important search boundaries

1. Exact match means identical bytes (SHA-256).
2. Perceptual match means similar low-frequency image structure (pHash/dHash/aHash). It helps with resizing and recompression but is not guaranteed for heavy crops, collages, rotations or visually related images.
3. The local match endpoint only searches images already indexed by the crawler.
4. Search-engine reverse-image services maintain their own indexes; ImageScout does not have unrestricted access to all images on the internet.
5. Reverse-image provider links open provider sites for manual image upload. Automated provider upload can break when provider interfaces, authentication or terms change.

## CLI

```powershell
# File/image footprint
python image_scout.py footprint .\photo.jpg

# Crawl a public site into the local index
python image_scout.py --db .\data\image_index.db crawl https://example.org --depth 2 --max-pages 50 --delay 1

# Search the local crawl index
python image_scout.py --db .\data\image_index.db match .\query.jpg --threshold 12 --limit 50

# Exact SHA-256 lookup
python image_scout.py --db .\data\image_index.db match <sha256_hex> --hash

# Index statistics
python image_scout.py --db .\data\image_index.db stats
```

## API endpoints

- `GET /api/health` — service, database and provider status
- `POST /api/footprint` — multipart `file`
- `POST /api/match` — multipart `file`, optional `threshold`, `limit`
- `GET /api/match/hash/{sha256}` — exact hash lookup
- `POST /api/web-search` — JSON `{ "query": "...", "count": 10 }`
- `POST /api/reverse` — multipart `file`; returns footprint and provider handoff links
- `POST /api/crawl` — JSON crawl request; enabled by default in local configuration
- `GET /api/stats` — image/page counts

## Database lifecycle

The SQLite database is stored at `data/image_index.db`. It is local and excluded from Git. It survives server restarts but is temporary in the sense that it is not a managed or remote database. To clear the index, stop the server and delete that file. Keep a copy if you want to retain crawl results.

## Operational constraints

- The crawler blocks non-public IP destinations by default and revalidates redirect destinations. Do not enable private-network crawling for arbitrary user-provided URLs.
- Respect each site's robots.txt, terms, copyright and rate limits. Do not bypass authentication or access controls.
- The local API is unauthenticated. Keep it on localhost unless you deliberately enable LAN access on a trusted private network.
- Do not expose the API directly to the public internet without authentication, rate limiting, TLS, hardened SSRF protections and isolated workers.
- EXIF GPS may reveal sensitive location data. Review metadata before sharing reports.
- The current SQLite scan is suitable for small/medium personal indexes; large-scale indexes need a dedicated similarity-search index and worker architecture.
