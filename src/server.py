import asyncio
import base64
import io
import json
import os
from contextlib import redirect_stdout

from dotenv import load_dotenv

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer, Image
except ModuleNotFoundError:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer, Image

from src.auth import BigBasketAuth
from src.order.bigbasket_order import BigBasketOrder

load_dotenv()

SERVE_SSE = os.environ.get("SERVE_HTTPS", "").lower() == "true"


SERVER_VERSION = "1.0.0"


def _build_server():
    """Build the server across mcp 1.x and 2.x, whose constructors differ."""
    for kwargs in (
        {"host": "0.0.0.0", "port": 8000} if SERVE_SSE else {"version": SERVER_VERSION},
        {},
    ):
        try:
            return MCPServer("bigbasket", **kwargs)
        except TypeError:
            continue
    return MCPServer("bigbasket")


mcp = _build_server()

SESSION_PATH = os.path.expanduser("~/.bigbasket_mcp/cookies/auth.json")


class BigBasketContext:
    """Holds one browser and one BigBasket session across tool calls."""

    def __init__(self):
        headless = os.environ.get("HEADLESS", "true").lower() == "true"
        self.auth = BigBasketAuth(headless=headless, session_path=SESSION_PATH)
        self.order: BigBasketOrder | None = None
        self._lock = asyncio.Lock()

    async def ensure_started(self):
        async with self._lock:
            restart = False
            if not self.auth.page:
                restart = True
            else:
                try:
                    restart = self.auth.page.is_closed()
                except Exception:
                    restart = True

            if restart:
                await self.auth.start_browser()
                self.order = BigBasketOrder(self.auth.page)
            elif self.order is None:
                self.order = BigBasketOrder(self.auth.page)

            # Keep the cookie file fresh so the login survives restarts.
            await self.auth.maybe_save_session()


ctx = BigBasketContext()


async def _run(coro_fn, *args, **kwargs):
    """Await a service call while capturing its prints for the model."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = await coro_fn(*args, **kwargs)
    return result, buffer.getvalue().strip()


def _format_products(items: list) -> list[str]:
    lines = []
    for item in items:
        stock = "" if item["in_stock"] else "  [out of stock]"
        mrp = (
            f" (MRP {item['mrp']})"
            if item["mrp"] and item["mrp"] != item["price"]
            else ""
        )
        pack = f"  {item['pack']}" if item["pack"] else ""
        lines.append(
            f"id={item['id']}  {item['name']}{pack}  Rs {item['price']}{mrp}{stock}"
        )
    return lines


# --- Account -----------------------------------------------------------------


@mcp.tool()
async def check_login() -> str:
    """Check whether the saved BigBasket session is still signed in.

    Call this before anything that needs an account: addresses, order history,
    checkout. Search and cart work for guests too.
    """
    await ctx.ensure_started()
    if await ctx.auth.is_logged_in():
        await ctx.auth.save_session()
        return "Logged in."
    return "Not logged in. Call login with the user's 10-digit phone number."


@mcp.tool()
async def login(phone_number: str) -> str:
    """Start BigBasket login. Sends an OTP to the given 10-digit phone number.

    Ask the user for the OTP and pass it to enter_otp.
    """
    await ctx.ensure_started()
    if await ctx.auth.is_logged_in():
        return "Already logged in with a valid session."
    _, logs = await _run(ctx.auth.login, phone_number)
    return logs or "Login started. Ask the user for the OTP sent to their phone."


@mcp.tool()
async def enter_otp(otp: str) -> str:
    """Submit the OTP BigBasket sent to the user's phone, completing login."""
    await ctx.ensure_started()
    _, logs = await _run(ctx.auth.enter_otp, otp)
    if await ctx.auth.is_logged_in():
        await ctx.auth.save_session()
        return (logs + "\nLogged in. Session saved.").strip()
    return (logs + "\nStill not logged in. The OTP may be wrong or expired.").strip()


@mcp.tool()
async def get_user_details() -> str:
    """Show who the current session belongs to, and the delivery point in use."""
    await ctx.ensure_started()
    logged_in = await ctx.auth.is_logged_in()
    info = await ctx.order.get_delivery_info()
    return json.dumps({"logged_in": logged_in, "delivery": info}, indent=2)


# --- Location and addresses --------------------------------------------------


@mcp.tool()
async def get_location_serviceability(latitude: float, longitude: float) -> str:
    """Check whether BigBasket delivers to a coordinate, and how fast.

    Price and stock are location bound, so set the location before searching.
    """
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.get_location_serviceability, latitude, longitude)
    return (logs + "\n" + json.dumps(result, indent=2)).strip()


@mcp.tool()
async def set_location(query: str) -> str:
    """Set the delivery location by area, landmark or pincode.

    Pass 'detect' to use this machine's own geolocation.
    """
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.set_location, query)
    return (logs + "\n" + str(result)).strip()


@mcp.tool()
async def get_delivery_info() -> str:
    """Show the address the session is shopping from and the current delivery ETA."""
    await ctx.ensure_started()
    return json.dumps(await ctx.order.get_delivery_info(), indent=2)


@mcp.tool()
async def list_saved_addresses() -> str:
    """List the account's saved delivery addresses. Requires a login."""
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.list_saved_addresses)
    if not result:
        return (logs or "No saved addresses were returned.").strip()
    return "\n".join(
        f"[{a['index']}] id={a['id']}  {a['label']}: {a['details']}"
        + ("  (selected)" if a["is_selected"] else "")
        for a in result
    )


@mcp.tool()
async def select_saved_address(address_id: str) -> str:
    """Switch the session to a saved address, using an id from list_saved_addresses."""
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.select_saved_address, address_id)
    return (logs + "\n" + str(result)).strip()


# --- Catalogue ---------------------------------------------------------------


@mcp.tool()
async def search_products(query: str, limit: int = 10, page: int = 1) -> str:
    """Search BigBasket for ONE product.

    Returns product ids for update_cart, with pack size, price and stock for the
    current delivery location. For several items at once use
    search_multiple_products. Search a concrete product name, not a dish: resolve
    a recipe into ingredients first.
    """
    await ctx.ensure_started()
    results, logs = await _run(ctx.order.search_products, query, limit, page)
    if not results:
        return (logs or f"No results for '{query}'.").strip()
    return "\n".join([f"Results for '{query}':"] + _format_products(results))


@mcp.tool()
async def search_multiple_products(queries: list[str], limit: int = 3) -> str:
    """Search several products in one call, one section per query.

    Use this whenever the user names more than one item, such as a shopping list
    or the ingredients of a dish.
    """
    await ctx.ensure_started()
    blocks = []
    for query in queries:
        results, _ = await _run(ctx.order.search_products, query, limit, 1)
        body = "\n".join(_format_products(results)) if results else "No results."
        blocks.append(f"## {query}\n{body}")
    return "\n\n".join(blocks)


@mcp.tool()
async def get_product_details(product_id: str) -> str:
    """Get full details for one product: price, pack, category, rating, description.

    The product id comes from search results.
    """
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.get_product_details, product_id)
    return (logs + "\n" + json.dumps(result, indent=2)).strip()


@mcp.tool()
async def suggest(term: str) -> str:
    """Get BigBasket's own search suggestions for a partial or vague term."""
    await ctx.ensure_started()
    suggestions = await ctx.order.suggest(term)
    return "\n".join(suggestions) if suggestions else f"No suggestions for '{term}'."


# --- Cart --------------------------------------------------------------------


@mcp.tool()
async def update_cart(cart_items: list[dict], replace_cart: bool = False) -> str:
    """Add, update or remove cart items in one call.

    Each entry takes `product_id` from search results and a `quantity`. Quantity 0
    removes the line. With `replace_cart` true, items missing from the list are
    removed first, so the cart ends up matching exactly what was passed.
    """
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.update_cart, cart_items, replace_cart)

    lines = []
    for update in result["updates"]:
        if update.get("ok"):
            lines.append(f"{update['name']} -> quantity {update['quantity']}")
        else:
            lines.append(f"FAILED: {update.get('name') or update.get('item')}")

    cart = result["cart"]
    lines.append(
        f"\nCart now holds {cart['item_count']} lines, {cart['units']} units. "
        f"Subtotal Rs {cart['subtotal']}"
        + (" (partial)" if cart["subtotal_is_partial"] else "")
    )
    return (logs + "\n" + "\n".join(lines)).strip()


@mcp.tool()
async def view_cart() -> str:
    """Show cart contents, quantities and the running subtotal.

    BigBasket's cart returns ids and quantities only, so names and prices come
    from products seen this session. The subtotal is flagged partial when a
    price is unknown.
    """
    await ctx.ensure_started()
    cart = await ctx.order.view_cart()
    if not cart["items"]:
        return "The cart is empty."

    lines = []
    for item in cart["items"]:
        label = f"{item['quantity']} x {item['name']} {item['pack']}".rstrip()
        money = (
            f"  Rs {item['line_total']}"
            if item["line_total"] is not None
            else "  price unknown"
        )
        lines.append(f"{label}{money}  (id={item['product_id']})")

    footer = (
        f"\n{cart['item_count']} lines, {cart['units']} units. "
        f"Subtotal Rs {cart['subtotal']}"
        + (" (partial: some prices unknown)" if cart["subtotal_is_partial"] else "")
    )
    return "\n".join(lines) + footer


@mcp.tool()
async def clear_cart() -> str:
    """Empty the cart."""
    await ctx.ensure_started()
    result, _ = await _run(ctx.order.clear_cart)
    return f"Removed {result['removed']} of {result['of']} cart lines."


# --- Orders ------------------------------------------------------------------


@mcp.tool()
async def get_past_order_items(limit: int = 30) -> str:
    """List items the user has bought before, from BigBasket's Smart Basket.

    Call this before searching a vague item so the search can use the exact
    product the user usually buys. Requires a login.
    """
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.get_past_order_items, limit)
    if result.get("error"):
        return result["error"]
    items = result.get("items") or []
    if not items:
        return result.get("note", "No past purchases were returned.")
    return "\n".join(_format_products(items))


@mcp.tool()
async def list_order_history(limit: int = 10) -> str:
    """List recent orders on the account. Requires a login."""
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.list_order_history, limit)
    return (logs + "\n" + json.dumps(result, indent=2)).strip()


@mcp.tool()
async def get_order_detail(order_id: str) -> str:
    """Get the detail of one order, including items and delivery status."""
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.get_order_detail, order_id)
    return (logs + "\n" + json.dumps(result, indent=2)[:4000]).strip()


# --- Checkout ----------------------------------------------------------------


@mcp.tool()
async def open_checkout() -> str:
    """Open the checkout page and report what it needs next."""
    await ctx.ensure_started()
    return await ctx.order.open_checkout()


@mcp.tool()
async def list_slots() -> str:
    """List the delivery slots offered on the checkout page."""
    await ctx.ensure_started()
    return await ctx.order.list_slots()


@mcp.tool()
async def get_payment_methods() -> str:
    """List the payment methods available for the current basket.

    Always offer these to the user and let them choose before placing an order.
    """
    await ctx.ensure_started()
    result, logs = await _run(ctx.order.get_payment_methods)
    return (logs + "\n" + json.dumps(result, indent=2)).strip()


@mcp.tool()
async def select_payment_method(method: str = "cod") -> str:
    """Select a payment method at checkout: cod, upi, wallet, card or netbanking."""
    await ctx.ensure_started()
    return await ctx.order.select_payment_method(method)


@mcp.tool()
async def create_order(confirm_order: bool = False) -> str:
    """Preview the order, or place it when confirm_order is true.

    Leave confirm_order false first: that returns the cart, address and checkout
    state for the user to review. Only pass true after the user has seen that
    preview and explicitly said to place the order. Confirming spends real money.
    """
    await ctx.ensure_started()
    if not confirm_order:
        cart = await ctx.order.view_cart()
        delivery = await ctx.order.get_delivery_info()
        checkout = await ctx.order.open_checkout()
        return json.dumps(
            {
                "preview": True,
                "cart": cart,
                "delivery": delivery,
                "checkout_state": checkout[:1200],
                "next_step": "Show this to the user. Call create_order(confirm_order=True) only after they confirm.",
            },
            indent=2,
        )
    return await ctx.order.place_order()


@mcp.tool()
async def check_payment_status() -> str:
    """Report where the checkout or payment flow currently stands."""
    await ctx.ensure_started()
    return json.dumps(await ctx.order.check_payment_status(), indent=2)


@mcp.tool()
async def screenshot():
    """Screenshot the current page, for when a flow needs the user to look at it."""
    await ctx.ensure_started()
    shot = await ctx.order.screenshot()
    return [
        f"Screenshot saved to {shot['path']}",
        Image(data=base64.b64decode(shot["base64"]), format=shot["format"]),
    ]
