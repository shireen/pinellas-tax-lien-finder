"""Daily Pinellas tax deed report: python -m taxlien.main [--no-email]"""
import argparse
import json
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
        name = liens.owner_search_name(p["owner"])
        if name:
            try:
                rows = clerk.search_name(name)
                p["liens"] = liens.screen(rows, p.get("legal") or p["gis_legal"])
            except Exception as e:  # noqa: BLE001
                p["liens"] = {"checked": False, "active_gov_liens": None, "gov_liens": None,
                              "notes": f"⚠️ Clerk search failed ({type(e).__name__}) — check manually"}
            time.sleep(1)
        else:
            p["liens"] = {"checked": False, "active_gov_liens": None, "gov_liens": None,
                          "notes": "⚠️ Check Clerk (owner is a trust or unsearchable name)"}
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
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true", help="build the report but don't send it")
    sys.exit(run(send_email=not ap.parse_args().no_email))
