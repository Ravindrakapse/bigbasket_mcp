from .base import BaseService

CHECKOUT_PATH = "/order/v2/checkout/"

METHOD_LABELS = {
    "cod": ["Cash on Delivery", "Pay on Delivery", "COD"],
    "upi": ["UPI", "Pay by UPI", "UPI / QR"],
    "wallet": ["bbWallet", "bb Wallet", "Wallet"],
    "card": ["Credit Card", "Debit Card", "Card"],
    "netbanking": ["Net Banking", "Netbanking"],
}


class PaymentService(BaseService):
    async def get_payment_methods(self) -> dict:
        """Payment options offered for the current basket."""
        await self.goto(CHECKOUT_PATH, wait_ms=6000)
        text = await self.page.evaluate("() => document.body.innerText")
        from .checkout import CheckoutService

        if not CheckoutService._checkout_rendered(text):
            return {
                "error": "Checkout did not render. Log in and put items in the cart first.",
                "page_said": text.strip()[:200],
            }

        available = []
        for key, labels in METHOD_LABELS.items():
            for label in labels:
                try:
                    if await self.page.locator(f"text={label}").first.count():
                        available.append({"method": key, "label": label})
                        break
                except Exception:
                    continue

        return {
            "methods": available or [{"method": "cod", "label": "Cash on Delivery"}],
            "note": "Options are read from the live checkout page and vary by cart and city.",
        }

    async def select_payment_method(self, method: str = "cod") -> str:
        labels = METHOD_LABELS.get(method.lower())
        if not labels:
            return f"Unknown payment method '{method}'. Use cod, upi, wallet, card or netbanking."

        await self.dismiss_overlays()
        for label in labels:
            option = self.page.locator(f"text={label}").first
            try:
                if await option.count() and await option.is_visible():
                    if await self.safe_click(option, f"payment option {label}"):
                        await self.page.wait_for_timeout(3000)
                        return f"Selected payment method: {label}."
            except Exception:
                continue
        return (
            f"No '{method}' option was visible. Call get_payment_methods first, "
            "or finish payment in the browser window."
        )

    async def check_payment_status(self) -> dict:
        """Where the checkout flow currently stands."""
        url = self.page.url
        text = await self.page.evaluate("() => document.body.innerText")
        lowered = text.lower()

        if "thank you" in lowered or "order placed" in lowered or "/thankyou" in url:
            state = "order_placed"
        elif "payment failed" in lowered or "transaction failed" in lowered:
            state = "failed"
        elif "checkout" in url:
            state = "awaiting_payment"
        else:
            state = "unknown"

        return {"state": state, "url": url, "page_text": text[:800]}
