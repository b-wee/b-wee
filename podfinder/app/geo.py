"""Location helpers: distance math, ZIP geocoding, and fee parsing.

ZIP lookup uses the ``zipcodes`` package, which bundles US ZIP → lat/lon
data offline — no API keys or network calls needed.
"""

import math
import re
from typing import Optional

import zipcodes

EARTH_RADIUS_MILES = 3958.8

ZIP_RE = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")
LATLON_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

RADIUS_CHOICES = [5, 10, 25, 50, 100]
DEFAULT_RADIUS_MILES = 25


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def zip_to_latlon(zip_code: str) -> Optional[tuple[float, float]]:
    """Look up the centroid of a US ZIP code. Returns None if unknown."""
    matches = zipcodes.matching(zip_code)
    if not matches:
        return None
    entry = matches[0]
    try:
        return float(entry["lat"]), float(entry["long"])
    except (KeyError, TypeError, ValueError):
        return None


def parse_location(text: Optional[str]) -> Optional[tuple[float, float]]:
    """Turn user location input into coordinates.

    Accepts a US ZIP code ("62701", "62701-1234") or raw "lat,lon" text
    (which is what the "use my location" button submits).
    """
    if not text:
        return None
    zip_match = ZIP_RE.match(text)
    if zip_match:
        return zip_to_latlon(zip_match.group(1))
    latlon_match = LATLON_RE.match(text)
    if latlon_match:
        lat, lon = float(latlon_match.group(1)), float(latlon_match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    return None


FEE_RE = re.compile(r"(\d+(?:\.\d{1,2})?)")


def parse_fee_cents(fee: Optional[str]) -> Optional[int]:
    """Parse an entry-fee string into cents.

    "Free" → 0, "$15" → 1500, "$7.50" → 750, None/unparseable → None.
    """
    if fee is None:
        return None
    if re.search(r"\bfree\b", fee, re.IGNORECASE):
        return 0
    match = FEE_RE.search(fee)
    if match:
        return round(float(match.group(1)) * 100)
    return None
