"""Smoke test against the live site with a guest session.

Covers search, product detail, batch cart update, cart read and cleanup.
It never logs in and never places an order.

    uv run python test/cli.py "toor dal"
"""

import asyncio
import json
import sys

from src.auth import BigBasketAuth
from src.order.bigbasket_order import BigBasketOrder


async def main(query: str):
    auth = BigBasketAuth(headless=True)
    await auth.start_browser()
    order = BigBasketOrder(auth.page)

    print("\n== delivery ==")
    print(json.dumps(await order.get_delivery_info(), indent=2))

    print(f"\n== search '{query}' ==")
    results = await order.search_products(query, limit=5)
    for item in results:
        print(f"  id={item['id']}  {item['name']}  {item['pack']}  Rs {item['price']}")
    if not results:
        await auth.close()
        sys.exit("Search returned nothing. The location may not be serviceable.")

    target = next((r for r in results if r["in_stock"]), results[0])

    print(f"\n== product detail for {target['id']} ==")
    details = await order.get_product_details(target["id"])
    print(json.dumps({k: v for k, v in details.items() if k != "page_text"}, indent=2)[:800])

    print("\n== update_cart: 2 units ==")
    result = await order.update_cart([{"product_id": target["id"], "quantity": 2}])
    print(json.dumps(result["updates"], indent=2))

    print("\n== view_cart ==")
    print(json.dumps(await order.view_cart(), indent=2))

    print("\n== cleanup ==")
    print(await order.clear_cart())
    print(await order.view_cart())

    await auth.close()
    print("\nSmoke test finished.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "toor dal"))
