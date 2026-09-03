import json
import os
import urllib.request

# Bangalore, BigBasket's home turf, as a last-resort fallback.
FALLBACK = {"latitude": 12.9716, "longitude": 77.5946}


def get_current_location():
    """Best-effort delivery coordinates.

    Order of preference: BB_LATITUDE/BB_LONGITUDE env vars, IP geolocation,
    then a Bangalore fallback. Never raises.
    """
    lat, lon = os.environ.get("BB_LATITUDE"), os.environ.get("BB_LONGITUDE")
    if lat and lon:
        try:
            return {"latitude": float(lat), "longitude": float(lon)}
        except ValueError:
            pass

    try:
        with urllib.request.urlopen("http://ip-api.com/json/", timeout=3) as response:
            data = json.loads(response.read().decode())
            if data.get("status") == "success":
                return {"latitude": data.get("lat"), "longitude": data.get("lon")}
    except Exception as e:
        print(f"IP geolocation failed: {e}")

    return FALLBACK
