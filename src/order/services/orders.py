from .base import BaseService

ORDER_LISTING = "/self-service-svc/v2/order-listing/"
ORDER_TABS = "/order/v1/order/tabs"
ORDERS_PAGE = "/member/active-orders/"
SMART_BASKET = "/listing-svc/v2/products?type=mem.sb&slug=all&page=1"


class OrderService(BaseService):
    """Order history and past purchases. Everything here needs a login."""

    def _listing_body(self, page: int = 1) -> dict:
        return {
            "context": "all",
            "group_by": "po",
            "old_system_orders": True,
            "request_type": "my_orders",
            "sort_by": "creation_date",
            "modifications": True,
            "exchange_order_supported": True,
            "additional_query_params": None,
            "ec_ids": ["10"],
            "page": page,
            "identifier": "bigbasket",
        }

    async def list_order_history(self, limit: int = 10) -> dict:
        """Recent orders, newest first."""
        # The service checks the referring page, so land on it first.
        if "/member/active-orders" not in self.page.url:
            try:
                await self.goto(ORDERS_PAGE, wait_ms=5000)
            except Exception as e:
                return {"error": f"Could not open the orders page: {e}"}

        status, payload = await self.api(
            ORDER_LISTING, method="POST", body=self._listing_body()
        )
        if status in (401, 403):
            return {"error": "Not logged in. Run login and enter_otp first."}
        if status != 200 or not isinstance(payload, dict):
            return {"error": f"Order listing returned HTTP {status}."}

        orders = [
            self._normalise_order(order, group)
            for group in payload.get("orders") or []
            for order in group.get("orders") or []
        ]

        return {
            "orders": orders[:limit],
            "total_pages": payload.get("total_pages"),
            "count": len(orders),
        }

    @staticmethod
    def _normalise_order(order: dict, group: dict) -> dict:
        member = group.get("member") or {}
        slot = order.get("slot") or {}
        address = ", ".join(
            str(part)
            for part in [member.get("area"), member.get("city"), member.get("pin")]
            if part
        )
        return {
            "order_id": order.get("order_id") or order.get("order_number"),
            "order_number": order.get("order_number"),
            "status": order.get("display_status") or order.get("status"),
            "placed_on": order.get("created_on"),
            "slot": slot.get("display_slot") or slot.get("display_date", ""),
            "total": order.get("final_total") or order.get("total_payable"),
            "savings": order.get("savings"),
            "item_count": order.get("items_count"),
            "payment": order.get("display_payment_method"),
            "payment_status": order.get("display_payment_status"),
            "deliver_to": address,
        }

    @staticmethod
    def _line_items(order: dict) -> list:
        """Flatten the delivered, undelivered and changed groups into one list."""
        items = []
        for bucket, entries in (order.get("line_items_grp") or {}).items():
            for line in (entries or {}).get("line_items") or []:
                brand = line.get("brand") or ""
                desc = line.get("desc") or ""
                items.append(
                    {
                        "sku": str(line.get("sku") or ""),
                        "name": " ".join(part for part in [brand, desc] if part).strip(),
                        "pack": line.get("weight") or "",
                        "quantity": line.get("quantity"),
                        "unit_price": line.get("sp"),
                        "line_total": line.get("total") or line.get("line_item_price"),
                        "savings": line.get("savings"),
                        "bucket": bucket,
                    }
                )
        return items

    async def get_order_detail(self, order_id: str) -> dict:
        """Detail for one order, including its line items."""
        if "/member/active-orders" not in self.page.url:
            try:
                await self.goto(ORDERS_PAGE, wait_ms=5000)
            except Exception as e:
                return {"error": f"Could not open the orders page: {e}"}

        wanted = str(order_id)
        for page in range(1, 4):
            status, payload = await self.api(
                ORDER_LISTING, method="POST", body=self._listing_body(page)
            )
            if status in (401, 403):
                return {"error": "Not logged in. Run login and enter_otp first."}
            if status != 200 or not isinstance(payload, dict):
                return {"error": f"Order listing returned HTTP {status}."}

            for group in payload.get("orders") or []:
                for order in group.get("orders") or []:
                    if wanted in (
                        str(order.get("order_id")),
                        str(order.get("order_number")),
                    ):
                        detail = self._normalise_order(order, group)
                        detail["items"] = self._line_items(order)
                        detail["delivered"] = (order.get("header_details") or {}).get("msg")
                        return detail

            if page >= int(payload.get("total_pages") or 1):
                break

        return {"error": f"No order found with id {order_id}."}

    async def get_past_order_items(self, limit: int = 30) -> dict:
        """Items bought before, which BigBasket surfaces as the Smart Basket."""
        from .search import SearchService

        status, payload = await self.api(SMART_BASKET)
        if status in (401, 403):
            return {"error": "Not logged in. Run login and enter_otp first."}
        if status != 200 or not isinstance(payload, dict):
            return {"error": f"Smart Basket returned HTTP {status}."}

        products = []
        for tab in payload.get("tabs") or []:
            products = (tab.get("product_info") or {}).get("products") or []
            if products:
                break

        items = [SearchService._normalise(p) for p in products[:limit]]
        if self.manager is not None:
            for item in items:
                self.manager.known_products[item["id"]] = {**item, "query": "smart basket"}

        if not items:
            return {"items": [], "note": "BigBasket returned no Smart Basket items yet."}
        return {"items": items}
