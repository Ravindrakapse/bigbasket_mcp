import base64
import os

from .base import BaseService

CHECKOUT_PATH = "/order/v2/checkout/"


class CheckoutService(BaseService):
    async def open_checkout(self) -> str:
        """Load the checkout page and report what it is asking for."""
        await self.goto(CHECKOUT_PATH, wait_ms=6000)
        text = await self.page.evaluate("() => document.body.innerText")

        if not self._checkout_rendered(text):
            return (
                "Checkout did not render. It needs a logged-in session and a cart "
                "with items. Run check_login, then add items, then try again.\n"
                f"Page URL: {self.page.url}\nPage said: {text.strip()[:200]}"
            )
        return f"Checkout page open at {self.page.url}\n\n{text[:1500]}"

    @staticmethod
    def _checkout_rendered(text: str) -> bool:
        """BigBasket serves guests and empty carts a stub page instead of checkout."""
        stripped = (text or "").strip()
        if len(stripped) < 120 or "Resource not found" in stripped:
            return False
        return "Login/" not in stripped[:800]

    async def list_slots(self) -> str:
        """Delivery slots offered on the checkout page, as rendered."""
        await self.goto(CHECKOUT_PATH, wait_ms=6000)
        slots = await self.page.evaluate(
            """() => [...document.querySelectorAll("[class*='lot'], [class*='Slot']")]
                     .map(e => e.innerText.trim())
                     .filter(t => t && t.length < 160)
                     .slice(0, 30)"""
        )
        return "\n".join(dict.fromkeys(slots)) or "No delivery slots found on the page."

    async def select_payment_method(self, method: str = "cod") -> str:
        """Pick a payment method on the checkout page.

        `cod` for cash on delivery, `upi` for UPI, `wallet` for the bb wallet.
        """
        labels = {
            "cod": ["Cash on Delivery", "Pay on Delivery", "COD"],
            "upi": ["UPI", "Pay by UPI", "UPI / QR"],
            "wallet": ["bbWallet", "bb Wallet", "Wallet"],
            "card": ["Credit", "Debit", "Card"],
        }.get(method.lower())

        if not labels:
            return f"Unknown payment method '{method}'. Use cod, upi, wallet or card."

        await self.dismiss_overlays()
        for label in labels:
            el = self.page.locator(f"text={label}").first
            try:
                if await el.count() and await el.is_visible():
                    if await self.safe_click(el, f"payment option {label}"):
                        await self.page.wait_for_timeout(3000)
                        return f"Selected payment method: {label}."
            except Exception:
                continue
        return (
            f"Could not find a '{method}' option on this page. "
            "Call open_checkout first, or finish payment in the browser window."
        )

    async def place_order(self) -> str:
        """Click the final confirm button. This spends real money."""
        await self.dismiss_overlays()
        for label in [
            "Place Order",
            "Confirm Order",
            "Proceed to Pay",
            "Pay Now",
            "Continue",
        ]:
            button = self.page.locator(f"button:has-text('{label}')").first
            try:
                if await button.count() and await button.is_visible():
                    if await self.safe_click(button, f"'{label}' button"):
                        await self.page.wait_for_timeout(8000)
                        text = await self.page.evaluate("() => document.body.innerText")
                        return f"Clicked '{label}'. Page now at {self.page.url}\n\n{text[:1200]}"
            except Exception:
                continue
        return "No order confirmation button was visible. Open checkout first."

    async def screenshot(self, path: str = None) -> dict:
        """Capture the current page so the user can see where the flow stands."""
        path = path or os.path.expanduser("~/.bigbasket_mcp/screenshot.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        await self.page.screenshot(path=path, full_page=False)
        with open(path, "rb") as handle:
            data = handle.read()
        return {"path": path, "base64": base64.b64encode(data).decode(), "format": "png"}
