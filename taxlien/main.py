"""Daily Pinellas tax deed report: python -m taxlien.main [--no-email]"""
import argparse
import json
import os
import sys
import time

from . import auctions, flood, liens, parcels, report, emailer
from .config import Config
from .http import session


def is_star(p, cfg) -> bool:
    return bool(
        p.get("address")
        and "VACANT" not in (p.get("use") or "").upper()
        and p.get("elevation_ft") is not None and p["elevation_ft"] >= cfg.min_elevation_ft
        and p.get("fema_zone") == "X"
        and p.get("flood_factor") is not None and p["flood_factor"] <= cfg.max_flood_factor
        and p.get("bid_ratio") is not None and p["bid_ratio"] <= cfg.max_bid_ratio
        and p["liens"].get("checked") and p["liens"]["active_gov_liens"] == 0
    )


def diff(prev: dict, props: list[dict], data: dict) -> list[str]:
    if not prev:
        return ["First run — no previous report to compare against."]
    old = {p["case"]: p for p in prev.get("properties", [])}
    new = {p["case"]: p for p in props}
    out = []
    for c in new.keys() - old.keys():
        out.append(f"New in range: {new[c]['address'] or c} (case {c}, auction {new[c]['auction_date']})")
    for c in old.keys() - new.keys():
        out.append(f"No longer listed: {old[c]['address'] or c} (case {c}) — likely redeemed, canceled or sold")
    for c in new.keys() & old.keys():
        a, b = old[c], new[c]
        if a.get("opening_bid") != b.get("opening_bid"):
            out.append(f"Opening bid changed for {b['address']}: ${a['opening_bid']:,.2f} → ${b['opening_bid']:,.2f}")
        if a.get("star") != b.get("star"):
            out.append(f"{b['address']} {'gained' if b['star'] else 'lost'} its ⭐")
        if a.get("liens", {}).get("gov_liens") != b["liens"].get("gov_liens"):
            out.append(f"Lien count changed for {b['address']}")
    old_dates = {d["date"] for d in prev.get("dates", [])}
    for d in data["dates"]:
        if d["date"] not in old_dates:
            out.append(f"New auction date posted: {d['date']} ({d['active']} properties)")
    return out or ["No changes."]


def _clerk_failure_reason(error: Exception) -> str:
    """Only expose structured, non-identifying diagnostics, never exception text."""
    stages = {"disclaimer", "search_form", "name_search", "name_selection", "partial_grid",
              "grid_results", "pagination", "parse", "search", "bootstrap", "results"}
    stage = getattr(error, "stage", None)
    status = getattr(error, "status_code", None)
    parts = [f"stage: {stage}" if isinstance(stage, str) and stage in stages else "stage: Clerk search"]
    if type(status) is int and 100 <= status <= 599:
        parts.append(f"HTTP {status}")
    code = getattr(error, "code", None)
    if code in {"access_denied", "http_error", "network_error", "unexpected_response",
                "incomplete_results", "invalid_record"}:
        parts.append(code.replace("_", " "))
    return "; ".join(parts)


def check_liens(clerk, owner: str, legal: str) -> dict:
    """Keep incomplete checks distinct from a completed zero-result screen."""
    name = liens.owner_search_name(owner)
    if not name:
        return {"checked": False, "active_gov_liens": None, "gov_liens": None,
                "failure_reason": "owner name unsearchable",
                "notes": "⚠️ Lien check incomplete: owner name unsearchable; check Clerk manually"}
    try:
        return liens.screen(clerk.search_name(name), legal)
    except Exception as error:  # noqa: BLE001
        reason = _clerk_failure_reason(error)
        return {"checked": False, "active_gov_liens": None, "gov_liens": None,
                "failure_reason": reason,
                "notes": f"⚠️ Lien check incomplete ({reason}); check Clerk manually"}
    finally:
        time.sleep(1)


def run(send_email: bool = True) -> int:
    cfg = Config.from_env()
    s = session()
    home = parcels.geocode(s, cfg.home_address)
    # Never log the home location or distances: Actions logs on a public repo are public.

    data = auctions.upcoming(cfg.months_ahead)
    print(f"Auctions: {data['dates']}")
    active = data["active"]
    gis = parcels.gis_parcels(s, [a["parcel_id"] for a in active if a["parcel_id"]])

    in_range = []
    for a in active:
        g = gis.get(a["parcel_id"])
        if not g:
            continue
        d = parcels.miles(home, (g["lon"], g["lat"]))
        if d <= cfg.radius_miles:
            in_range.append({**a, "distance_mi": round(d, 2), "strap": g["STRAP"], "owner": g.get("OWNER1") or "",
                             "lon": g["lon"], "lat": g["lat"], "gis_legal": g.get("LEGAL") or ""})
    print(f"{len(in_range)} of {len(active)} active properties in range")

    clerk = liens.Clerk()
    for p in in_range:
        p.update(parcels.pcpao_details(s, p["strap"]))
        mv = p.get("market_value")
        p["bid_ratio"] = round(p["opening_bid"] / mv, 4) if mv and p.get("opening_bid") else None
        p["elevation_ft"] = flood.usgs_elevation_ft(s, p["lon"], p["lat"])
        p["fema_zone"], p["fema_subtype"] = flood.fema_zone(s, p["lon"], p["lat"])
        if p["address"]:
            full = f"{p['address']}, {p['city']}, FL {p['zip']}"
            p["flood_factor"], p["flood_factor_url"] = flood.flood_factor(s, full, p["lon"], p["lat"])
        else:
            p["flood_factor"], p["flood_factor_url"] = None, None
        p["liens"] = check_liens(clerk, p["owner"], p.get("legal") or p["gis_legal"])
        p["star"] = is_star(p, cfg)

    print(f"{sum(p['star'] for p in in_range)} star picks")
    in_range.sort(key=lambda p: (not p["star"], -(p.get("elevation_ft") or 0)))

    prev = {}
    if cfg.state_file.exists():
        prev = json.loads(cfg.state_file.read_text())
    changes = diff(prev, in_range, data)

    subject, html = report.build(cfg, data, in_range, changes)
    cfg.output_html.parent.mkdir(parents=True, exist_ok=True)
    cfg.output_html.write_text(html)
    print(f"Wrote {cfg.output_html}")

    public = [{k: v for k, v in p.items() if k not in ("lon", "lat")} for p in in_range]
    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.state_file.write_text(json.dumps({"dates": data["dates"], "properties": public}, indent=1))

    if send_email:
        emailer.send(cfg, subject, html)
    # Preserve the comparison baseline after a complete report, even when its
    # health status is nonzero. Do not mark generation complete after an email error.
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("report_generated=true\n")
    incomplete = sum(not p.get("liens", {}).get("checked") for p in in_range)
    if incomplete:
        print(f"INCOMPLETE: {incomplete} lien checks require manual review; report written.")
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true", help="build the report but don't send it")
    sys.exit(run(send_email=not ap.parse_args().no_email))
