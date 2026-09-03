from .base import BaseService

CART_GET = "/basketService/get/"
CART_SET = "/mapi/v4.2.0/c-set-i/"
CART_INCR = "/mapi/v4.2.0/c-incr-i/"
CART_DECR = "/mapi/v4.2.0/c-decr-i/"


class CartService(BaseService):
    async def get_cart_map(self) -> dict:
        """Raw cart contents as {sku_id: quantity}."""
        status, payload = await self.api(CART_GET)
        if status != 200 or not isinstance(payload, dict):
            print(f"Could not read the cart (HTTP {status}).")
            return {}
        return {str(k): int(v) for k, v in (payload.get("cartInfo") or {}).items()}

    def _body(self, product_id: str, quantity: int) -> dict:
        sku = int(product_id)
        body = {
            "prod_id": sku,
            "qty": quantity,
            "_bb_client_type": "web",
            "inv_info": {"skus": [{"id": sku, "qty": quantity}]},
        }
        known = (self.manager.known_products if self.manager else {}).get(str(product_id))
        if known and known.get("query"):
            body["term"] = known["query"]
            body["term_source"] = "ps"
        return body

    async def set_quantity(self, product_id: str, quantity: int) -> dict:
        """Set the absolute quantity for one SKU. Quantity 0 removes it."""
        quantity = max(0, int(quantity))
        status, payload = await self.api(
            CART_SET, method="POST", body=self._body(product_id, quantity)
        )
        ok = status == 200 and isinstance(payload, dict) and payload.get("status") == "OK"
        if not ok:
            print(f"Cart update failed for {product_id} (HTTP {status}): {str(payload)[:200]}")
            return {"ok": False, "product_id": str(product_id), "quantity": None}

        response = payload.get("response") or {}
        total = (response.get("sku") or {}).get("tot_qty", quantity)
        return {
            "ok": True,
            "product_id": str(product_id),
            "quantity": total,
            "cart_items": (response.get("c_summary") or {}).get("num_items"),
        }

    async def add_to_cart(self, product_id: str, quantity: int = 1) -> dict:
        """Add to whatever is already in the cart for this SKU."""
        current = (await self.get_cart_map()).get(str(product_id), 0)
        known = (self.manager.known_products if self.manager else {}).get(str(product_id))
        target = current + max(1, int(quantity))

        cap = (known or {}).get("max_quantity") or 0
        if cap and target > cap:
            print(f"BigBasket caps this item at {cap}. Requesting {cap} instead of {target}.")
            target = cap

        result = await self.set_quantity(product_id, target)
        if result["ok"] and known:
            result["name"] = known.get("name")
            result["price"] = known.get("price")
        return result

    async def remove_from_cart(self, product_id: str, quantity: int = 1) -> dict:
        """Remove `quantity` units, or everything when quantity is 0 or negative."""
        current = (await self.get_cart_map()).get(str(product_id), 0)
        if current == 0:
            return {"ok": True, "product_id": str(product_id), "quantity": 0}
        target = 0 if int(quantity) <= 0 else max(0, current - int(quantity))
        return await self.set_quantity(product_id, target)

    async def clear_cart(self) -> dict:
        cart = await self.get_cart_map()
        removed = 0
        for product_id in cart:
            result = await self.set_quantity(product_id, 0)
            removed += 1 if result["ok"] else 0
        return {"removed": removed, "of": len(cart)}

    async def view_cart(self) -> dict:
        """Cart contents enriched with names and prices seen during this session."""
        cart = await self.get_cart_map()
        known = self.manager.known_products if self.manager else {}

        items, total, unpriced = [], 0.0, 0
        for product_id, quantity in cart.items():
            info = known.get(product_id, {})
            price = info.get("price") or ""
            line = None
            try:
                line = round(float(price) * quantity, 2)
                total += line
            except (TypeError, ValueError):
                unpriced += 1
            items.append(
                {
                    "product_id": product_id,
                    "name": info.get("name") or f"SKU {product_id}",
                    "pack": info.get("pack", ""),
                    "quantity": quantity,
                    "price": price,
                    "line_total": line,
                }
            )

        return {
            "items": items,
            "item_count": len(items),
            "units": sum(cart.values()),
            "subtotal": round(total, 2),
            "subtotal_is_partial": unpriced > 0,
        }

    async def update_cart(self, cart_items: list, replace_cart: bool = False) -> dict:
        """Apply several cart changes at once.

        Each entry needs `product_id` and `quantity`; quantity 0 removes the line.
        With `replace_cart`, anything not listed is removed first.
        """
        results = []

        if replace_cart:
            keep = {str(item.get("product_id")) for item in cart_items}
            for product_id in await self.get_cart_map():
                if product_id not in keep:
                    await self.set_quantity(product_id, 0)

        for item in cart_items:
            product_id = str(item.get("product_id") or item.get("id") or "")
            if not product_id:
                results.append({"ok": False, "error": "Missing product_id", "item": item})
                continue
            quantity = int(item.get("quantity", 1))
            result = await self.set_quantity(product_id, quantity)
            known = (self.manager.known_products if self.manager else {}).get(product_id, {})
            result["name"] = item.get("name") or known.get("name") or f"SKU {product_id}"
            results.append(result)

        return {"updates": results, "cart": await self.view_cart()}
