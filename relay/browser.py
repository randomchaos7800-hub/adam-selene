"""Web scraping via Firecrawl.

Firecrawl handles JS-rendered pages, bot protection, and returns clean markdown.

API key: set FIRECRAWL_API_KEY in environment or config/secrets.env.
Free tier: https://firecrawl.dev
"""

import base64
import logging
import os

import requests

logger = logging.getLogger(__name__)

FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"


def _get_api_key() -> str | None:
    """Read Firecrawl API key from environment."""
    return os.environ.get("FIRECRAWL_API_KEY")


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def browse_url(url: str, **_) -> dict:
    """Scrape a URL and return clean markdown content via Firecrawl."""
    api_key = _get_api_key()
    if not api_key:
        return {"success": False, "error": "Firecrawl API key not configured. Set FIRECRAWL_API_KEY in environment."}

    try:
        resp = requests.post(
            f"{FIRECRAWL_BASE}/scrape",
            headers=_headers(api_key),
            json={"url": url, "formats": ["markdown"]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            return {"success": False, "error": data.get("error", "Firecrawl scrape failed")}

        content = data["data"].get("markdown", "")
        metadata = data["data"].get("metadata", {})
        title = metadata.get("title", url)
        final_url = metadata.get("sourceURL", url)

        # Truncate to 8000 chars
        if len(content) > 8000:
            content = content[:8000] + f"\n\n[Truncated — {len(content)} chars total]"

        return {"success": True, "url": final_url, "title": title, "content": content}

    except requests.Timeout:
        return {"success": False, "error": f"Timeout scraping {url}"}
    except Exception as e:
        logger.error(f"browse_url failed: {e}")
        return {"success": False, "error": str(e)}


def screenshot_url(url: str, full_page: bool = False, **_) -> dict:
    """Screenshot a URL via Firecrawl. Returns base64-encoded PNG."""
    api_key = _get_api_key()
    if not api_key:
        return {"success": False, "error": "Firecrawl API key not configured. Set FIRECRAWL_API_KEY in environment."}

    try:
        resp = requests.post(
            f"{FIRECRAWL_BASE}/scrape",
            headers=_headers(api_key),
            json={"url": url, "formats": ["screenshot"]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            return {"success": False, "error": data.get("error", "Screenshot failed")}

        screenshot_url_result = data["data"].get("screenshot")
        metadata = data["data"].get("metadata", {})
        title = metadata.get("title", url)

        if not screenshot_url_result:
            return {"success": False, "error": "No screenshot returned by Firecrawl"}

        # Download the hosted screenshot and convert to base64
        img_resp = requests.get(screenshot_url_result, timeout=15)
        img_resp.raise_for_status()
        b64 = base64.b64encode(img_resp.content).decode("utf-8")

        return {
            "success": True,
            "url": url,
            "title": title,
            "image_b64": b64,
            "media_type": "image/png",
        }

    except requests.Timeout:
        return {"success": False, "error": f"Timeout screenshotting {url}"}
    except Exception as e:
        logger.error(f"screenshot_url failed: {e}")
        return {"success": False, "error": str(e)}


def browser_interact(url: str, actions: list, screenshot_after: bool = True, **_) -> dict:
    """Perform interactive browser actions via Firecrawl, then return final page content.

    Actions are executed in sequence. Supported types:
      click       - {"type": "click", "selector": "button#submit"}
      write       - {"type": "write", "text": "hello", "selector": "#input"}
      wait        - {"type": "wait", "milliseconds": 1000}
      scroll      - {"type": "scroll", "direction": "down", "amount": 3}
      screenshot  - {"type": "screenshot"}
    """
    api_key = _get_api_key()
    if not api_key:
        return {"success": False, "error": "Firecrawl API key not configured. Set FIRECRAWL_API_KEY in environment."}

    # Normalize action keys from older conventions
    normalized = []
    for action in actions:
        a = dict(action)
        # fill -> write
        if a.get("type") == "fill":
            a["type"] = "write"
        # ms -> milliseconds
        if "ms" in a and "milliseconds" not in a:
            a["milliseconds"] = a.pop("ms")
        # value -> text
        if "value" in a and "text" not in a:
            a["text"] = a.pop("value")
        normalized.append(a)

    formats = ["markdown"]
    if screenshot_after:
        formats.append("screenshot")

    try:
        resp = requests.post(
            f"{FIRECRAWL_BASE}/scrape",
            headers=_headers(api_key),
            json={"url": url, "actions": normalized, "formats": formats},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            return {"success": False, "error": data.get("error", "Firecrawl interact failed")}

        page_data = data.get("data", {})
        content = page_data.get("markdown", "")
        metadata = page_data.get("metadata", {})
        title = metadata.get("title", url)
        final_url = metadata.get("sourceURL", url)

        if len(content) > 8000:
            content = content[:8000] + f"\n\n[Truncated — {len(content)} chars total]"

        result = {"success": True, "url": final_url, "title": title, "content": content}

        if screenshot_after:
            screenshot_url_result = page_data.get("screenshot")
            if screenshot_url_result:
                try:
                    img_resp = requests.get(screenshot_url_result, timeout=15)
                    img_resp.raise_for_status()
                    result["image_b64"] = base64.b64encode(img_resp.content).decode("utf-8")
                    result["media_type"] = "image/png"
                except Exception as e:
                    logger.warning(f"Screenshot download failed (non-fatal): {e}")

        return result

    except requests.Timeout:
        return {"success": False, "error": f"Timeout during browser_interact on {url}"}
    except Exception as e:
        logger.error(f"browser_interact failed: {e}")
        return {"success": False, "error": str(e)}
