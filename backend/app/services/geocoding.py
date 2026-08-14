"""
Reverse geocoding: turns a raw GPS coordinate into a real, human-readable
area name (e.g. "Koramangala, Bengaluru") — used to give an agent a real
"my area" on their profile from their own device's GPS, rather than
asking them to type a zone name in by hand.

Uses OpenStreetMap's Nominatim (https://nominatim.org) — free, no API
key, no billing account, which matters for a zero-budget project.
Nominatim's usage policy requires a descriptive User-Agent identifying
the application (not a browser UA string) and a max of ~1 request/second
from a single client; both are respected here — this endpoint is only
ever called once per agent per "detect my area" click, nowhere near
that limit.
"""

import requests

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "DeliverySync-PortfolioProject/1.0 (reverse geocoding for agent area assignment)"
REQUEST_TIMEOUT_SECONDS = 6


def reverse_geocode_area(latitude: float, longitude: float) -> str | None:
    """
    Returns a short, human-readable area name for a coordinate, or None
    if it couldn't be resolved (network issue, rate limit, or a
    coordinate with no nearby address data — the middle of the ocean,
    for instance). Callers should treat None as "try again," not a crash.

    Prefers the smallest well-known locality Nominatim returns
    (suburb/neighbourhood), falling back to progressively broader ones,
    so an agent in a big city gets "Koramangala" rather than just
    "Bengaluru" when that finer-grained data is available — that's the
    level of detail that actually helps a dispatcher tell two agents in
    the same city apart when matching them to a delivery zone.
    """
    try:
        response = requests.get(
            NOMINATIM_REVERSE_URL,
            params={
                "format": "json",
                "lat": latitude,
                "lon": longitude,
                "zoom": 14,  # suburb/neighbourhood level of detail
                "addressdetails": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    address = data.get("address") or {}

    locality = (
        address.get("suburb")
        or address.get("neighbourhood")
        or address.get("quarter")
        or address.get("city_district")
        or address.get("town")
        or address.get("village")
    )
    city = address.get("city") or address.get("county") or address.get("state")

    if locality and city and locality != city:
        return f"{locality}, {city}"
    if locality:
        return locality
    if city:
        return city

    # Nothing structured enough to use — the raw display_name is a full
    # postal-style address, too verbose to be a useful "area", so this
    # counts as a failed lookup rather than falling back to it.
    return None
