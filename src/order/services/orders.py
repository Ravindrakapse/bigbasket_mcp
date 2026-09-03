from .base import BaseService

ACTIVE_ORDERS = "/member/active-orders"
ORDER_DETAIL = "/order/v1/order/{order_id}/detail"


class OrderService(BaseService):
    """Order history and past purchases. Everything here needs a login."""

    async def list_order_history(self, limit: int = 10) -> dict:
        status, payload = await self.api(ACTIVE_ORDERS)
        if status == 200 and isinstance(payload, dict):
            orders = payload.get("orders") or payload.get("data") or []
            if orders:
                return {"orders": self._normalise_orders(orders)[:limit]}
        if status in (401, 403):
            return {"error": "Not logged in. Run login and enter_otp first."}

        # The API shape varies by account, so read the orders page instead.
        try:
            await self.goto("/member/order-list/", wait_ms=5000)
            text = await self.page.evaluate("() => document.body.innerText")
            if "Login/" in text[:800]:
                return {"error": "Not logged in. Run login and enter_otp first."}
            return {"orders": [], "page_text": text[:1500]}
        except Exception as e:
            return {"error": f"Could not read order history: {e}"}

    @staticmethod
    def _normalise_orders(orders: list) -> list:
        out = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            out.append(
                {
                    "order_id": order.get("order_id") or order.get("id"),
                    "status": order.get("order_status") or order.get("status"),
                    "placed_on": order.get("order_date") or order.get("created_on"),
                    "total": order.get("order_total") or order.get("total"),
                    "item_count": order.get("item_count") or order.get("num_items"),
                }
            )
        return out

    async def get_order_detail(self, order_id: str) -> dict:
        status, payload = await self.api(ORDER_DETAIL.format(order_id=order_id))
        if status == 200 and isinstance(payload, dict):
            return payload
        if status in (401, 403):
            return {"error": "Not logged in. Run login and enter_otp first."}
        try:
            await self.goto(f"/member/order-details/{order_id}/", wait_ms=5000)
            text = await self.page.evaluate("() => document.body.innerText")
            return {"order_id": order_id, "page_text": text[:1500]}
        except Exception as e:
            return {"error": f"Could not read order {order_id}: {e}"}

    async def get_past_order_items(self, limit: int = 30) -> dict:
        """Items bought before, which BigBasket surfaces as the Smart Basket."""
        status, payload = await self.api(
            "/listing-svc/v2/products?type=sb&slug=smart-basket&page=1"
        )
        products = []
        if status == 200 and isinstance(payload, dict):
            from .search import SearchService

            for tab in payload.get("tabs") or []:
                products = (tab.get("product_info") or {}).get("products") or []
                if products:
                    break
            items = [SearchService._normalise(p) for p in products[:limit]]
            if self.manager is not None:
                for item in items:
                    self.manager.known_products[item["id"]] = {**item, "query": "smart basket"}
            if items:
                return {"items": items}

        if status in (401, 403):
            return {"error": "Not logged in. Run login and enter_otp first."}
        return {"items": [], "note": "No past purchases were returned for this account."}
