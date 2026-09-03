import json
import re

from .base import BaseService
from .search import SearchService


class ProductService(BaseService):
    """Detail lookups for a single SKU.

    BigBasket has no public product-by-id JSON endpoint, so this reads the
    product page and pulls the payload Next.js embeds in it.
    """

    async def get_product_details(self, product_id: str) -> dict:
        known = (self.manager.known_products if self.manager else {}).get(str(product_id), {})
        url = known.get("url") or f"/pd/{product_id}/"

        try:
            await self.goto(url, wait_ms=3500)
        except Exception as e:
            return {"error": f"Could not open the product page: {e}"}

        raw = await self.page.evaluate(
            """() => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }"""
        )

        product = None
        if raw:
            try:
                product = self._find_product(json.loads(raw), str(product_id))
            except json.JSONDecodeError:
                product = None

        if product:
            details = SearchService._normalise(product)
            details.update(
                {
                    "description": self._clean(
                        (product.get("additional_info") or {}).get("about_the_product")
                        or (product.get("p_desc") or "")
                    ),
                    "category": (product.get("category") or {}).get("llc_name", ""),
                    "rating": (product.get("rating_info") or {}).get("avg_rating"),
                    "rating_count": (product.get("rating_info") or {}).get("rating_count"),
                    "units_sold": product.get("number_of_skus_sold", ""),
                }
            )
            if self.manager is not None:
                self.manager.known_products[details["id"]] = {
                    **self.manager.known_products.get(details["id"], {}),
                    **details,
                }
            return details

        # Fall back to what the rendered page shows.
        text = await self.page.evaluate("() => document.body.innerText")
        return {
            "id": str(product_id),
            "name": known.get("name") or (await self.page.title()),
            "url": self.page.url,
            "page_text": text[:1200],
            "note": "Structured product data was unavailable, so this is the page as rendered.",
        }

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"<[^>]+>", " ", str(value or "")).strip()[:800]

    @staticmethod
    def _find_product(payload, product_id: str, depth: int = 0):
        """Walk the embedded JSON for the product object matching this id."""
        if depth > 12:
            return None
        if isinstance(payload, dict):
            if str(payload.get("id")) == product_id and "pricing" in payload:
                return payload
            for value in payload.values():
                found = ProductService._find_product(value, product_id, depth + 1)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = ProductService._find_product(value, product_id, depth + 1)
                if found:
                    return found
        return None
