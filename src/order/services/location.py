from .base import BaseService

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
            raw = payload.get("addresses") or payload.get("data") or payload.get("address_list") or []
        elif isinstance(payload, list):
            raw = payload

        addresses = []
        for index, address in enumerate(raw):
            if not isinstance(address, dict):
                continue
            parts = [
                address.get("house_number"),
                address.get("street"),
                address.get("area"),
                address.get("city"),
                address.get("pin"),
            ]
            addresses.append(
                {
                    "index": index,
                    "id": address.get("id") or address.get("address_id"),
                    "label": address.get("label") or address.get("address_label") or "Address",
                    "details": ", ".join(str(p) for p in parts if p),
                    "is_selected": bool(address.get("is_selected") or address.get("selected")),
                }
            )
        return addresses

    async def select_address(self, address_id: str) -> str:
        """Switch the shopping session to a saved address."""
        for path, body in [
            ("/ui-svc/v1/address/select", {"address_id": str(address_id)}),
            ("/ui-svc/v1/address/set", {"id": str(address_id)}),
        ]:
            status, payload = await self.api(path, method="POST", body=body)
            if status == 200:
                await self.goto("/", wait_ms=4000)
                return f"Delivery address set to {address_id}."
        return (
            f"Could not switch to address {address_id} through the API. "
            "Use set_location, or pick the address in the browser window."
        )

    async def set_location(self, query: str) -> str:
        """Set the delivery location by searching for an area, landmark or pincode.

        Pass 'detect' to use the browser's geolocation instead.
        """
        await self.goto("/", wait_ms=4000)
        await self.dismiss_overlays()

        opened = False
        for selector in [
            "button:has-text('Select Location')",
            "div:has-text('Deliver to') >> nth=-1",
            "[class*='AddressDropdown']",
            "[class*='LocationBar']",
            "header button:has-text('Bangalore')",
        ]:
            try:
                el = self.page.locator(selector).first
                if await el.count() and await el.is_visible():
                    if await self.safe_click(el, "location selector"):
                        opened = True
                        break
            except Exception:
                continue
        if not opened:
            return "Could not open the location picker. The site layout may have changed."

        await self.page.wait_for_timeout(2000)

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
                    info = await self.get_delivery_info()
                    return f"Detected location applied. Current delivery info: {info}"
            return "The location picker has no 'Detect my location' option right now."

        try:
            box = await self.page.wait_for_selector(
                "input[placeholder*='ocation'], input[placeholder*='pincode'], "
                "input[placeholder*='area'], input[type='search']",
                state="visible",
                timeout=15000,
            )
            await box.click()
            await box.fill(query)
            await self.page.wait_for_timeout(3000)

            suggestion = self.page.locator("li, [role='option'], [class*='uggestion']").first
            if await suggestion.count():
                await self.safe_click(suggestion, "location suggestion")
                await self.page.wait_for_timeout(5000)
                info = await self.get_delivery_info()
                return f"Location set from '{query}'. Current delivery info: {info}"
            return f"No location suggestions appeared for '{query}'."
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
