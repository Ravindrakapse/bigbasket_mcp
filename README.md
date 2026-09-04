# bigbasket-mcp

An MCP server that lets Claude shop on [BigBasket](https://www.bigbasket.com): search the
catalogue, build a cart, pick a delivery address and drive checkout.

It runs a real Chromium session against bigbasket.com and calls BigBasket's own web APIs
from inside that page. Cookies, CSRF tokens and Akamai bot-manager telemetry are therefore
maintained by the site's own JavaScript, which is what makes the calls succeed. The same
requests issued straight from Python are rejected.

## What it can do

Tool names follow the same shape as the official Zepto MCP server, so the two are
interchangeable in a prompt.

| Area | Tools |
| --- | --- |
| Account | `check_login`, `login`, `enter_otp`, `get_user_details` |
| Location | `get_location_serviceability`, `set_location`, `get_delivery_info`, `list_saved_addresses`, `select_saved_address` |
| Catalogue | `search_products`, `search_multiple_products`, `get_product_details`, `suggest` |
| Cart | `update_cart`, `view_cart`, `clear_cart` |
| Orders | `get_past_order_items`, `list_order_history`, `get_order_detail` |
| Checkout | `open_checkout`, `list_slots`, `get_payment_methods`, `select_payment_method`, `create_order`, `check_payment_status`, `screenshot` |

Search and cart work for a guest session. Addresses, order history, checkout and
ordering need a login.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/<you>/bigbasket-mcp.git
cd bigbasket-mcp
uv sync
uv run playwright install chromium
```

## Configure

Add the server to your MCP client. For Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "bigbasket": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/bigbasket-mcp", "main.py"],
      "env": { "HEADLESS": "true" }
    }
  }
}
```

Environment variables, all optional, are documented in `.env.sample`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEADLESS` | `true` | Set `false` to watch the browser, which helps during login and payment |
| `SERVE_HTTPS` | unset | Set `true` to serve over SSE on port 8000 instead of stdio |
| `BB_LATITUDE`, `BB_LONGITUDE` | IP lookup | Pin the delivery coordinates |
| `BB_MIN_CALL_INTERVAL` | `0.8` | Minimum seconds between API calls; raise it if BigBasket returns 429 |
| `BB_ENTRY_CONTEXT`, `BB_ENTRY_CONTEXT_ID` | `bbnow`, `10` | `bbnow`/`10` is 10-minute delivery, `bb-b2c`/`100` is standard slotted delivery |

The login session is stored at `~/.bigbasket_mcp/cookies/auth.json` and reused across runs.

## Usage

A typical flow, in the order the tools expect:

1. `set_location("Koramangala")`, `set_location("detect")` or
   `get_location_serviceability(12.93, 77.62)`, since price and stock are location bound.
2. `login("9876543210")`, then `enter_otp("1234")`.
3. `get_past_order_items()` to learn which brands the user actually buys.
4. `search_products("toor dal")`, or `search_multiple_products(["toor dal", "basmati rice"])`
   for a whole shopping list, to get product ids.
5. `update_cart([{"product_id": "40147597", "quantity": 2}])`, then `view_cart()`.
6. `get_payment_methods()`, `select_payment_method("cod")`.
7. `create_order()` for a preview, then `create_order(confirm_order=True)` once the user
   has seen it and said yes.

`create_order(confirm_order=True)` spends real money. Show the preview and get an
explicit go-ahead first.

## Smoke test

Exercises search, cart add, cart read and cleanup against the live site as a guest:

```bash
uv run python test/cli.py "toor dal"
```

## How it works

| Concern | Endpoint |
| --- | --- |
| Search | `GET /listing-svc/v2/products?type=ps&slug={query}&page={n}` |
| Suggestions | `GET /listing-svc/v1/product/term-completion?term={term}` |
| Cart read | `GET /basketService/get/` |
| Cart write | `POST /mapi/v4.2.0/c-set-i/`, with `c-incr-i` and `c-decr-i` as relatives |
| Address and ETA | `GET /ui-svc/v2/header/?send_door_info=true&send_address_set_by_user=true` |
| Address book | `GET /ui-svc/v1/address/list`, which returns 401 for guests |
| Past purchases | `GET /listing-svc/v2/products?type=mem.sb&slug=all` (Smart Basket) |
| Orders | `POST /self-service-svc/v2/order-listing/` |
| Switching address | `PUT /member-svc/v2/member/current-delivery-address/` |
| Checkout | `/order/v2/checkout/`, driven through the page |

The cart endpoint returns only SKU ids and quantities, so `view_cart` labels lines using
products seen during the session and flags the subtotal as partial when a price is unknown.

Login, location picking and checkout are driven through the page rather than the API,
because those flows involve OTP entry, geolocation prompts and payment widgets.

`set_location` is for guests. On a logged-in account BigBasket's picker rewrites the
selected saved address to whatever point is picked, so the tool refuses to run there
unless `allow_address_edit` is passed, and points at `select_saved_address` instead.

Service areas follow the browser's coordinates, not the chosen address id, so
switching to a saved address also moves the geolocation pin to that address's
own latitude and longitude. Skipping that leaves search answering 303 "SAs got
filtered".

Writes to the newer services need the `x-csurftoken` header, whose value the site keeps
in a cookie of the same name; the server reads it out of the page on every call.

Bursts are throttled: BigBasket answers a fast run of calls with 429, so the server
spaces calls and backs off before retrying. A long shopping list therefore takes a few
seconds per item rather than running flat out.

Two details matter for reliability. The server launches the full Chromium build rather
than Playwright's headless shell, which BigBasket's bot protection answers with 403. And
it waits for that protection to mint its session cookie before the first API call, then
reloads and retries once if a call still comes back 403.

## Notes

- Unofficial. It uses BigBasket's private web endpoints, which can change without warning.
- Use it with your own account, at a request rate a person could plausibly produce.
- No credentials are stored beyond the browser session file that BigBasket itself issues.

## License

MIT
