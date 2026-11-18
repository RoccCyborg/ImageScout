from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from footprint import analyze_footprint, hamming_distance, similarity_percent
from crawler import extract_links_and_images


def test_footprint_hashes_and_image_metadata(tmp_path: Path):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (64, 48), (20, 80, 140)).save(image_path)
    result = analyze_footprint(image_path)
    assert result["is_image"] is True
    assert len(result["crypto"]["sha256"]) == 64
    assert result["perceptual_hashes"]["phash"]
    assert result["image_metadata"]["width"] == 64
    assert result["image_metadata"]["height"] == 48


def test_hamming_distance_and_similarity():
    assert hamming_distance("0000", "0000") == 0
    assert hamming_distance("0000", "000f") == 4
    assert similarity_percent(0) == 100.0


def test_html_image_extraction():
    html = """<html><head><title>Demo</title>
    <meta property="og:image" content="/cover.jpg"></head>
    <body><img src="/a.jpg" alt="A"><img data-src="/lazy.webp">
    <div style="background-image:url('/bg.png')"></div>
    <a href="/page">Next</a></body></html>"""
    title, links, images = extract_links_and_images(
        html, "https://example.org/start"
    )
    assert title == "Demo"
    assert "https://example.org/page" in links
    urls = {url for url, _ in images}
    assert "https://example.org/a.jpg" in urls
    assert "https://example.org/lazy.webp" in urls
    assert "https://example.org/cover.jpg" in urls
    assert "https://example.org/bg.png" in urls
