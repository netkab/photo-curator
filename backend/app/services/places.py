"""Google Places API (New) — READ-ONLY.

Endpoints used (all v1, Places API New):
  POST /v1/places:searchNearby   — reverse-geocode a GPS coordinate to the nearest place
  POST /v1/places:searchText     — text search for a place (Maps Studio manual lookup)
  GET  /v1/places/{id}           — fetch full place details by id

Field-masking (X-Goog-FieldMask) keeps every call in the cheapest billing SKU:
  - id, displayName, formattedAddress, googleMapsUri  → Essentials SKU ($0.017/req)
  - rating, userRatingCount, types, primaryType       → Essentials SKU
  - regularOpeningHours, editorialSummary             → Pro SKU ($0.068/req) — NOT requested

There is NO API to post a review. This module is strictly read-only.
Without GOOGLE_PLACES_API_KEY all functions return None/[] gracefully.
"""
from __future__ import annotations

import logging
import requests

from ..config import settings

log = logging.getLogger(__name__)

# Places API (New) base endpoints
_BASE    = "https://places.googleapis.com/v1"
_NEARBY  = f"{_BASE}/places:searchNearby"
_TEXT    = f"{_BASE}/places:searchText"
_DETAILS = f"{_BASE}/places/{{place_id}}"

# Fields kept within Essentials SKU (cheapest tier)
_FIELDS_BASIC   = "places.id,places.displayName,places.formattedAddress,places.googleMapsUri"
_FIELDS_SEARCH  = f"{_FIELDS_BASIC},places.rating,places.userRatingCount,places.types,places.primaryType"
_FIELDS_DETAILS = "id,displayName,formattedAddress,googleMapsUri,rating,userRatingCount,types,primaryType,location"


def _headers(field_mask: str) -> dict:
    """Standard headers for every Places API (New) request."""
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.places_api_key,
        "X-Goog-FieldMask": field_mask,
    }


def _parse_place(p: dict) -> dict:
    """Normalise a Places API (New) place object to a flat dict."""
    return {
        "place_id":         p.get("id"),
        "name":             (p.get("displayName") or {}).get("text"),
        "address":          p.get("formattedAddress"),
        "maps_uri":         p.get("googleMapsUri"),
        "rating":           p.get("rating"),
        "rating_count":     p.get("userRatingCount"),
        "primary_type":     p.get("primaryType"),
        "types":            p.get("types", []),
    }


def nearest_place(lat: float, lng: float, radius_m: int = 150) -> dict | None:
    """Return the nearest place to (lat, lng) within radius_m metres, or None."""
    if not settings.places_api_key:
        return None
    try:
        body = {
            "maxResultCount": 1,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": radius_m,
                }
            },
        }
        r = requests.post(_NEARBY, json=body, headers=_headers(_FIELDS_BASIC), timeout=15)
        r.raise_for_status()
        places = r.json().get("places", [])
        return _parse_place(places[0]) if places else None
    except requests.HTTPError as e:
        log.warning("Places nearby error %s: %s", e.response.status_code, e.response.text[:200])
        return None
    except Exception as e:
        log.warning("Places nearby exception: %s", e)
        return None


def nearby_places(lat: float, lng: float, radius_m: int = 200, max_results: int = 10) -> list[dict]:
    """Return up to ``max_results`` places near (lat, lng) — the on-demand suggestion list a user
    picks from to match an 'Unmatched place' cluster. One request = one Essentials-SKU billing unit."""
    if not settings.places_api_key:
        return []
    try:
        body = {
            "maxResultCount": max(1, min(max_results, 20)),
            "rankPreference": "DISTANCE",
            "locationRestriction": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}
            },
        }
        r = requests.post(_NEARBY, json=body, headers=_headers(_FIELDS_SEARCH), timeout=15)
        r.raise_for_status()
        return [_parse_place(p) for p in r.json().get("places", [])]
    except requests.HTTPError as e:
        log.warning("Places nearby-list error %s: %s", e.response.status_code, e.response.text[:200])
        return []
    except Exception as e:
        log.warning("Places nearby-list exception: %s", e)
        return []


def search_text(query: str, lat: float | None = None, lng: float | None = None) -> list[dict]:
    """Text search for up to 5 places, optionally biased toward (lat, lng)."""
    if not settings.places_api_key:
        return []
    try:
        body: dict = {"textQuery": query, "maxResultCount": 5}
        if lat is not None and lng is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": 5000,
                }
            }
        r = requests.post(_TEXT, json=body, headers=_headers(_FIELDS_SEARCH), timeout=15)
        r.raise_for_status()
        return [_parse_place(p) for p in r.json().get("places", [])]
    except requests.HTTPError as e:
        log.warning("Places text-search error %s: %s", e.response.status_code, e.response.text[:200])
        return []
    except Exception as e:
        log.warning("Places text-search exception: %s", e)
        return []


def get_place(place_id: str) -> dict | None:
    """Fetch full details for a known place_id."""
    if not settings.places_api_key or not place_id:
        return None
    try:
        url = _DETAILS.format(place_id=place_id)
        r = requests.get(url, headers=_headers(_FIELDS_DETAILS), timeout=15)
        r.raise_for_status()
        return _parse_place(r.json())
    except requests.HTTPError as e:
        log.warning("Places details error %s: %s", e.response.status_code, e.response.text[:200])
        return None
    except Exception as e:
        log.warning("Places details exception: %s", e)
        return None


def review_url(place_id: str | None, name: str | None, maps_uri: str | None = None) -> str:
    """Best URL for the user to manually post a review.

    Preference order:
    1. Direct write-review link via place_id (most reliable)
    2. The place's googleMapsUri from the API (opens the place page)
    3. Text search fallback
    """
    if place_id:
        return f"https://search.google.com/local/writereview?placeid={place_id}"
    if maps_uri:
        return maps_uri
    if name:
        from urllib.parse import quote_plus
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(name)}"
    return "https://maps.google.com"
