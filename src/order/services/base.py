import asyncio
import json
import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

from playwright.async_api import Page

if TYPE_CHECKING:
    from src.order.bigbasket_order import BigBasketOrder

BASE_URL = "https://www.bigbasket.com"

# BigBasket throttles bursts with 429, so calls are spaced and backed off.
MIN_CALL_INTERVAL = float(os.environ.get("BB_MIN_CALL_INTERVAL", "0.8"))
RATE_LIMIT_BACKOFF = (3.0, 8.0, 20.0)

_last_call_at = 0.0
_call_lock = asyncio.Lock()


class BaseService:
    """Shared plumbing: every BigBasket API call runs inside the live page.

    BigBasket sits behind Akamai Bot Manager, so requests issued from Python
    are rejected. Running `fetch` in the page reuses the browser's cookies,
    session and bot-manager tokens, which the site keeps refreshed.
    """

    def __init__(self, page: Page, manager: Optional["BigBasketOrder"] = None):
        self.page = page
        self.manager = manager

    def _headers(self) -> dict:
        return {
            "x-channel": "BB-WEB",
            "x-tracker": str(uuid.uuid4()),
            "content-type": "application/json",
            "x-entry-context": os.environ.get("BB_ENTRY_CONTEXT", "bbnow"),
            "x-entry-context-id": os.environ.get("BB_ENTRY_CONTEXT_ID", "10"),
        }

    async def api(
        self, path: str, method: str = "GET", body: Any = None, retry: bool = True
    ) -> tuple[int, Any]:
        """Call a BigBasket endpoint. Returns (status, parsed body or raw text).

        A 403 means Akamai has not blessed this session yet, so the page is
        reloaded once to let the sensor script re-run.
        """
        status, payload = await self._paced_fetch(path, method, body)

        attempt = 0
        while status == 429 and retry and attempt < len(RATE_LIMIT_BACKOFF):
            wait = RATE_LIMIT_BACKOFF[attempt]
            print(f"Rate limited by BigBasket. Waiting {wait:.0f}s before retrying.")
            await self.page.wait_for_timeout(int(wait * 1000))
            status, payload = await self._paced_fetch(path, method, body)
            attempt += 1

        if status == 403 and retry:
            print("Got 403 from BigBasket. Reloading the page and retrying once.")
            try:
                await self.page.reload(wait_until="domcontentloaded", timeout=60000)
                await self.page.wait_for_timeout(6000)
            except Exception as e:
                print(f"Reload failed: {e}")
            status, payload = await self._paced_fetch(path, method, body)
        return status, payload

    async def _paced_fetch(
        self, path: str, method: str = "GET", body: Any = None
    ) -> tuple[int, Any]:
        """Keep a minimum gap between calls so bursts do not trip the throttle."""
        global _last_call_at
        async with _call_lock:
            gap = time.monotonic() - _last_call_at
            if gap < MIN_CALL_INTERVAL:
                await self.page.wait_for_timeout(int((MIN_CALL_INTERVAL - gap) * 1000))
            _last_call_at = time.monotonic()
        return await self._fetch(path, method, body)

    async def _fetch(
        self, path: str, method: str = "GET", body: Any = None
    ) -> tuple[int, Any]:
        result = await self.page.evaluate(
            """async ({path, method, body, headers}) => {
                const options = {method, headers, credentials: 'include'};
                if (body !== null && body !== undefined) options.body = JSON.stringify(body);
                const res = await fetch(path, options);
                const text = await res.text();
                return {status: res.status, text};
            }""",
            {
                "path": path,
                "method": method,
                "body": body,
                "headers": self._headers(),
            },
        )
        status, text = result["status"], result["text"]
        try:
            return status, json.loads(text) if text else None
        except json.JSONDecodeError:
            return status, text

    async def goto(self, path: str, wait_ms: int = 4000):
        url = path if path.startswith("http") else BASE_URL + path
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(wait_ms)

    async def dismiss_overlays(self):
        """Close any modal or toast that blocks interaction. Never raises."""
        for selector in [
            "button[aria-label='Close']",
            "button[aria-label='close']",
            "div[class*='Modal'] button[class*='close']",
            "button:has-text('✕')",
            "button:has-text('×')",
        ]:
            try:
                el = self.page.locator(selector).first
                if await el.count() and await el.is_visible():
                    await el.click(timeout=2000)
                    await self.page.wait_for_timeout(300)
            except Exception:
                continue

    async def safe_click(self, locator, description: str = "element") -> bool:
        """Click with scroll, force and JS-click fallbacks."""
        try:
            await locator.scroll_into_view_if_needed(timeout=5000)
            await self.page.wait_for_timeout(200)
        except Exception:
            pass
        for attempt, action in (
            ("normal", lambda: locator.click(timeout=8000)),
            ("force", lambda: locator.click(force=True, timeout=5000)),
            ("js", lambda: locator.evaluate("el => el.click()")),
        ):
            try:
                await action()
                return True
            except Exception as e:
                print(f"{attempt} click failed on {description}: {e}")
        return False
