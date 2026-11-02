"""Flood risk from three sources: USGS elevation, FEMA flood zone, First Street Flood Factor."""
import re

from .http import get

EPQS = "https://epqs.nationalmap.gov/v1/json"
NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
FS = "https://firststreet.org"


def usgs_elevation_ft(s, lon: float, lat: float):
    try:
        v = get(s, EPQS, params={"x": lon, "y": lat, "units": "Feet", "wkid": 4326}).json().get("value")
        return round(float(v), 1) if v not in (None, "") and float(v) > -1000 else None
    except Exception:  # noqa: BLE001
        return None


def fema_zone(s, lon: float, lat: float):
    try:
        r = get(s, NFHL, params={
            "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "FLD_ZONE,ZONE_SUBTY",
            "returnGeometry": "false", "f": "json"}).json()
        feats = r.get("features", [])
        if not feats:
            return None, None
        a = feats[0]["attributes"]
        return a.get("FLD_ZONE"), (a.get("ZONE_SUBTY") or "").title()
    except Exception:  # noqa: BLE001
        return None, None


def flood_factor(s, address: str, lon: float, lat: float):
    """Return (score 1-10, url) from First Street, or (None, None)."""
    try:
        res = get(s, f"{FS}/api/ac", params={"searchTerm": address, "lat": lat, "lng": lon}).json()
        prop = next((x for x in res if x.get("entity") == "property"), None)
        if not prop:
            return None, None
        url = f"{FS}/property/address/{prop['fsid']}_fsid/0"
        html = get(s, url).text
        m = re.search(r"(\d+)/10(?:<!-- -->)?\s*Flood Factor", html)
        return (int(m.group(1)) if m else None), url
    except Exception:  # noqa: BLE001
        return None, None


def band(elev):
    if elev is None:
        return "—"
    return "🔴" if elev < 5 else "🟠" if elev < 10 else "🟡" if elev < 20 else "🟢"
