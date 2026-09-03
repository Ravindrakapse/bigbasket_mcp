import urllib.parse

from .base import BaseService

# BigBasket's product listing service. type=ps is a plain product search.
SEARCH_PATH = "/listing-svc/v2/products?type=ps&slug={slug}&page={page}"


class SearchService(BaseService):
    @staticmethod
    def _price(product: dict) -> tuple[str, str]:
        """Returns (selling price, MRP) as plain strings."""
        discount = (product.get("pricing") or {}).get("discount") or {}
        primary = discount.get("prim_price") or {}
        return str(primary.get("sp") or ""), str(discount.get("mrp") or "")

    @classmethod
    def _normalise(cls, product: dict) -> dict:
        brand = (product.get("brand") or {}).get("name") or ""
        availability = product.get("availability") or {}
        sp, mrp = cls._price(product)
        pack = " ".join(
            part
            for part in [product.get("w") or "", product.get("pack_desc") or ""]
            if part
        ).strip()
        name = " ".join(part for part in [brand, product.get("desc") or ""] if part)
        return {
            "id": str(product.get("id")),
            "name": name or product.get("desc") or "Unknown product",
            "brand": brand,
            "pack": pack,
            "price": sp,
            "mrp": mrp,
            "in_stock": availability.get("avail_status") == "001",
            "eta": availability.get("short_eta") or "",
            "max_quantity": product.get("sku_max_quantity") or 0,
            "url": product.get("absolute_url") or "",
        }

    @staticmethod
    def _extract_products(payload) -> list:
        if not isinstance(payload, dict):
            return []
        for tab in payload.get("tabs") or []:
            products = (tab.get("product_info") or {}).get("products")
            if products:
                return products
        return []

    async def search_products(self, query: str, limit: int = 10, page: int = 1) -> list:
        """Search the catalogue for the current delivery location."""
        slug = urllib.parse.quote(query)
        status, payload = await self.api(SEARCH_PATH.format(slug=slug, page=page))

        if status != 200:
            message = payload
            if isinstance(payload, dict) and payload.get("errors"):
                message = payload["errors"][0].get("msg", payload["errors"])
            print(f"Search failed with HTTP {status}: {str(message)[:200]}")
            return []

        products = self._extract_products(payload)
        results = []
        for product in products[:limit]:
            item = self._normalise(product)
            results.append(item)
            if self.manager is not None:
                self.manager.known_products[item["id"]] = {**item, "query": query}
        return results

    async def suggest(self, term: str) -> list:
        """Search-as-you-type suggestions: matching products, brands and categories."""
        status, payload = await self.api(
            "/listing-svc/v1/product/term-completion?term=" + urllib.parse.quote(term)
        )
        if status != 200 or not isinstance(payload, dict):
            return []

        suggestions = []
        for key, label in (
            ("products", "product"),
            ("brands", "brand"),
            ("categories", "category"),
        ):
            section = payload.get(key)
            values = section.get("values") if isinstance(section, dict) else section
            for value in (values or [])[:8]:
                name = value.get("name") if isinstance(value, dict) else str(value)
                if name:
                    suggestions.append(f"{name}  [{label}]")
        return suggestions
