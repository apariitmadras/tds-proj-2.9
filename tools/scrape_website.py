from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
import httpx
from playwright.async_api import async_playwright

async def scrape_website(
    url: str,
    output_file: str = "outputs/scraped_content.html",
    wait_until: str = "domcontentloaded",
    timeout_ms: int = 60000,
) -> Dict[str, Any]:
    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Fast path: Wikipedia is static — try plain HTTP first (usually <1s)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            r = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.text) > 10000:
                out.write_text(r.text, encoding="utf-8")
                return {"ok": True, "file": str(out), "url": url, "engine": "httpx"}
    except Exception:
        pass  # fallback to browser if HTTP path fails

    # Fallback: headless Chromium (slower)
    launch_args = ["--no-sandbox", "--disable-setuid-sandbox"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=launch_args)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            html = await page.content()
            out.write_text(html, encoding="utf-8")
            return {"ok": True, "file": str(out), "url": url, "engine": "playwright"}
        finally:
            await browser.close()
