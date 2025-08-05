# tools/scrape_website.py
# Playwright-based scraper (Chromium). Saves page HTML to a file.

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from playwright.async_api import async_playwright


async def scrape_website(
    url: str,
    output_file: str = "outputs/scraped_content.html",
    wait_until: str = "domcontentloaded",
    timeout_ms: int = 60_000,
) -> Dict[str, Any]:
    """
    Scrape a URL with headless Chromium and save HTML to output_file.
    Returns: {"ok": bool, "file": str, "url": str}
    """
    launch_args = ["--no-sandbox", "--disable-setuid-sandbox"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=launch_args)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            html = await page.content()
            out = Path(output_file)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            return {"ok": True, "file": str(out), "url": url}
        finally:
            await browser.close()
