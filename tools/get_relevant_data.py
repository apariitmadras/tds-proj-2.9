# tools/get_relevant_data.py
# BeautifulSoup extractor using a CSS selector.

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, List
from bs4 import BeautifulSoup


def get_relevant_data(file_name: str, js_selector: Optional[str] = None) -> Dict[str, Any]:
    """
    Extract text from a saved HTML file using a CSS selector.
    Returns: {"data": list[str] | str, "count": int?, "selector": str?}
    """
    html = Path(file_name).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    if js_selector:
        els = soup.select(js_selector)
        return {
            "data": [el.get_text(strip=True) for el in els],
            "count": len(els),
            "selector": js_selector,
        }

    return {"data": soup.get_text(separator=" ", strip=True)}
