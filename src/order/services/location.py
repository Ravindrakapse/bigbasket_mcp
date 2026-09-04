import re

from .base import BaseService

CURRENT_ADDRESS = "/member-svc/v2/member/current-delivery-address/"

HEADER = "/ui-svc/v2/header/?send_door_info=true&send_address_set_by_user=true"
ADDRESS_LIST = "/ui-svc/v1/address/list"


class LocationService(BaseService):
    async def get_delivery_info(self) -> dict:
        """Which address and delivery door the session is currently shopping from."""
        status, payload = await self.api(HEADER)
        if status != 200 or not isinstance(payload, dict):
            return {"error": f"Header call returned HTTP {status}."}

        service_areas = payload.get("sa_list") or []
        return {
            "selected_address_id": payload.get("selected_address_id"),
            "address_label": (payload.get("address") or {}).get("display_name")
            if isinstance(payload.get("address"), dict)
            else payload.get("address"),
            "eta": next((sa.get("eta") for sa in service_areas if sa.get("eta")), ""),
            "service_areas": [
                {"sa_id": sa.get("sa_id"), "eta": sa.get("eta")} for sa in service_areas
            ],
        }

    async def get_saved_addresses(self) -> list:
        """Address book. Requires a logged-in session."""
        status, payload = await self.api(ADDRESS_LIST)
        if status in (401, 403):
            print("Not logged in, so the address book is unavailable. Run login first.")
            return []
        if status != 200:
            print(f"Address list returned HTTP {status}.")
            return []

        raw = []
        if isinstance(payload, dict):
            raw = (
                payload.get("addresses")
                or payload.get("data")
                or payload.get("address_list")
                or []
            )
        elif isinstance(payload, list):
            raw = payload

        addresses = []
        for index, address in enumerate(raw):
            if not isinstance(address, dict):
                continue
            parts = [
                address.get("address1"),
                address.get("address2"),
                address.get("area"),
                address.get("city_name"),
                address.get("pin"),
            ]
            addresses.append(
                {
                    "index": index,
                    "id": address.get("id") or address.get("address_id"),
                    "label": address.get("nick") or address.get("address_label") or "Address",
                    "details": ", ".join(str(p).strip() for p in parts if p),
                    "is_selected": bool(address.get("is_current_delivery")),
                    "lat": address.get("lat"),
                    "lng": address.get("lng"),
                }
            )
        return addresses

    async def select_address(self, address_id: str) -> str:
        """Switch the shopping session to a saved address.

        This is the call the site makes when you pick an address in the header
        picker. It also rebuilds the service-area cookies that search depends on.
        """
        try:
            wanted = int(str(address_id).strip())
        except ValueError:
            return f"'{address_id}' is not a valid address id."

        # The site uses PUT here; POST is answered with 405.
        status, payload = await self.api(
            CURRENT_ADDRESS,
            method="PUT",
            body={"address_id": wanted, "return_hub_cookies": False},
        )
        if status in (401, 403):
            return "Not logged in. Run login and enter_otp first."
        if status not in (200, 204):
            return f"Could not switch to address {address_id}: HTTP {status}."

        # BigBasket resolves service areas from the browser's coordinates, not
        # from the address id alone, so move the pin to the address as well.
        moved = ""
        for address in await self.get_saved_addresses():
            if str(address["id"]) == str(wanted) and address["lat"] and address["lng"]:
                try:
                    await self.page.context.set_geolocation(
                        {
                            "latitude": float(address["lat"]),
                            "longitude": float(address["lng"]),
                        }
                    )
                    moved = f" ({address['label']}: {address['details']})"
                except Exception as e:
                    print(f"Could not move the geolocation pin: {e}")
                break

        await self.goto("/", wait_ms=6000)
        return (
            f"Delivery address set to {address_id}{moved}. "
            f"{await self.get_delivery_info()}"
        )

    async def set_location(self, query: str, allow_address_edit: bool = False) -> str:
        """Set the delivery location by searching for an area, landmark or pincode.

        Pass 'detect' to use the browser's geolocation instead.

        On a logged-in account the picker does not just move the shopping
        location: it rewrites the currently selected saved address to the picked
        point. That silently edits the user's address book, so this refuses to
        run while logged in unless allow_address_edit is set. Use
        select_saved_address to move between saved addresses instead.
        """
        status, _ = await self.api(ADDRESS_LIST)
        logged_in = status == 200
        if logged_in and not allow_address_edit:
            addresses = await self.get_saved_addresses()
            listing = "\n".join(
                f"  {a['id']}  {a['label']}: {a['details']}" for a in addresses
            )
            return (
                "Refusing to run: on a logged-in account this picker overwrites the "
                "selected saved address with the location picked, changing the "
                "user's address book. Use select_saved_address with one of these "
                f"instead:\n{listing}\n"
                "Pass allow_address_edit=True only if the user has explicitly asked "
                "to change that saved address."
            )

        await self.goto("/", wait_ms=5000)
        await self.dismiss_overlays()

        # The header chip reads "Delivery in N mins / <pincode>, <city>", or
        # "Select Location" before a location is chosen. Clicking it opens the
        # picker panel.
        opened = False
        # The chip's wording changes with the ETA: "Delivery in 16 mins",
        # "Get it in 4 hrs", or "Select Location" before one is chosen.
        chip = self.page.locator("header button").filter(
            has_text=re.compile(r"Delivery in|Get it in|Select ?[Ll]ocation|Deliver to", re.I)
        )
        try:
            if await chip.count():
                await chip.first.click(timeout=8000)
                opened = True
        except Exception:
            opened = False
        if not opened:
            return "Could not open the location picker. The site layout may have changed."

        await self.page.wait_for_timeout(3000)

        if query.strip().lower() == "detect":
            for selector in [
                "button:has-text('Detect my location')",
                "button:has-text('Use current location')",
                "text=Detect my location",
            ]:
                el = self.page.locator(selector).first
                if await el.count() and await el.is_visible():
                    await self.safe_click(el, "detect location")
                    await self.page.wait_for_timeout(5000)
                    return f"Detected location applied. {await self.get_delivery_info()}"
            return "The location picker has no 'Detect my location' option right now."

        try:
            box = await self.page.wait_for_selector(
                "input[placeholder*='area or street'], input[placeholder*='area'], "
                "input[placeholder*='pincode'], input[placeholder*='ocation']",
                state="visible",
                timeout=15000,
            )
            await box.click()
            # The suggestion list is keystroke driven, so a bulk fill returns nothing.
            await box.type(query, delay=110)
            await self.page.wait_for_timeout(4000)

            # Scope to the picker's own visible list: other hidden <li> elements
            # elsewhere on the page also carry commas.
            options = self.page.locator("li:visible").filter(has_text=",")
            count = await options.count()
            if not count:
                return (
                    f"No location suggestions came back for '{query}'. "
                    "Try a broader area name or a pincode."
                )

            chosen = (await options.first.inner_text()).replace("\n", " ").strip()
            await options.first.click()
            await self.page.wait_for_timeout(6000)
            return f"Location set to '{chosen}'. {await self.get_delivery_info()}"
        except Exception as e:
            return f"Could not set the location: {e}"

    async def get_location_serviceability(self, latitude: float, longitude: float) -> dict:
        """Check whether BigBasket delivers to a coordinate, and how fast.

        Sets the browser's geolocation to the point, reloads, and reports the
        delivery door BigBasket resolves it to.
        """
        try:
            await self.page.context.set_geolocation(
                {"latitude": float(latitude), "longitude": float(longitude)}
            )
        except Exception as e:
            return {"serviceable": None, "error": f"Could not set coordinates: {e}"}

        await self.goto("/", wait_ms=5000)
        info = await self.get_delivery_info()

        status, payload = await self.api(
            "/listing-svc/v2/products?type=ps&slug=milk&page=1"
        )
        has_catalogue = False
        if status == 200 and isinstance(payload, dict):
            for tab in payload.get("tabs") or []:
                if (tab.get("product_info") or {}).get("products"):
                    has_catalogue = True
                    break

        return {
            "serviceable": has_catalogue,
            "latitude": latitude,
            "longitude": longitude,
            "eta": info.get("eta", ""),
            "selected_address_id": info.get("selected_address_id"),
            "note": ""
            if has_catalogue
            else "No catalogue was returned, so this point looks unserviceable.",
        }
