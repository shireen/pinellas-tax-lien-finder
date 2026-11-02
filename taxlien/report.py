"""Build the HTML email."""
import datetime as dt
from html import escape

from .auctions import preview_url
from .flood import band
from .parcels import PCPAO

TD = 'style="border:1px solid #ddd;padding:6px;vertical-align:top;font-size:13px"'
TH = 'style="border:1px solid #ddd;padding:6px;background:#f3f3f3;font-size:12px;text-align:left"'
COLS = ["Case #", "Auction", "Address", "Dist", "Parcel ID", "Opening Bid", "Market Value", "Bid/MV",
        "Elev (USGS)", "FEMA Zone", "Flood Factor", "Liens / Mortgages"]


def _money(v, cents=False):
    if v is None:
        return "—"
    return f"${v:,.2f}" if cents else f"${v:,.0f}"


def _ff(p):
    v = p.get("flood_factor")
    if v is None:
        return "—"
    col = "#16a34a" if v <= 1 else "#d97706" if v <= 4 else "#dc2626"
    return f"<a href='{p['flood_factor_url']}' style='color:{col};font-weight:bold'>{v}/10</a>"


def _row(p):
    star = "⭐ " if p["star"] else ""
    desc = ", ".join(x for x in [p.get("use", ""), f"{p['living_sf']} sf" if p.get("living_sf") else "",
                                 p.get("year_built", "")] if x)
    ratio = f"{p['bid_ratio'] * 100:.0f}%" if p.get("bid_ratio") is not None else "—"
    elev = p.get("elevation_ft")
    fema = f"{p['fema_zone']} ({p['fema_subtype']})" if p.get("fema_subtype") else (p.get("fema_zone") or "—")
    cells = [
        f"<a href='{preview_url(p['auction_date'])}'>{escape(p['case'])}</a>",
        p["auction_date"],
        f"{star}<b>{escape(p['address'] or '(no address)')}, {escape(p['city'])} {p['zip']}</b>"
        f"<br><span style='color:#666'>{escape(desc)}</span>",
        f"{p['distance_mi']:.2f} mi",
        f"<a href='{PCPAO}{p['strap']}'>{p['parcel_id']}</a>",
        _money(p["opening_bid"], True), _money(p.get("market_value")), ratio,
        f"{band(elev)} {elev} ft" if elev is not None else "—",
        escape(fema), _ff(p), escape(p["liens"]["notes"]),
    ]
    return "<tr>" + "".join(f"<td {TD}>{c}</td>" for c in cells) + "</tr>"


def _table(props):
    head = "".join(f"<th {TH}>{c}</th>" for c in COLS)
    return f"<table style='border-collapse:collapse;width:100%'><tr>{head}</tr>" + \
        "".join(_row(p) for p in props) + "</table>"


def build(cfg, data, props, changes) -> tuple[str, str]:
    stars = [p for p in props if p["star"]]
    rest = [p for p in props if not p["star"]]
    today = dt.date.today()
    dates = ", ".join(f"<b>{d['date']}</b> ({d['active']} active)" for d in data["dates"]) or "none scheduled"
    subject = (f"Pinellas Tax Deed Auction — {today:%m/%d/%Y} — "
               f"{'⭐ ' + str(len(stars)) + ' Picks, ' if stars else ''}{len(props)} Properties Within "
               f"{cfg.radius_miles:g} mi")
    new_html = "<br>".join(escape(c) for c in changes) if changes else "No changes."
    picks = "".join(
        f"<li><b>{escape(p['address'])}</b> ({p['distance_mi']:.1f} mi): bid {p['bid_ratio'] * 100:.0f}% of value, "
        f"{p['elevation_ft']} ft, FEMA {p['fema_zone']}, Flood Factor {p['flood_factor']}. "
        f"{escape(p['liens']['notes'])}</li>" for p in stars)
    html = f"""<div style="font-family:Arial,sans-serif;max-width:1100px">
<p>Pinellas County tax deed auctions within <b>{cfg.radius_miles:g} miles</b> of the home address.</p>
<p style="background:#eef6ff;padding:8px;border-left:4px solid #3b82f6"><b>What's new since the last run:</b><br>{new_html}</p>
<p><b>Summary:</b> upcoming auctions: {dates}. <b>{len(props)}</b> properties are in range; <b>{len(stars)}</b> earn a ⭐.</p>
{"<h3>🔍 ⭐ Picks</h3><ul>" + picks + "</ul>" if stars else "<p>No properties meet every ⭐ rule today.</p>"}
{"<h3>⭐ Picks</h3>" + _table(stars) if stars else ""}
<h3>📋 All Properties Within {cfg.radius_miles:g} Miles</h3>{_table(stars + rest)}
<p style="font-size:12px;color:#555">
<b>Flood key (USGS elevation):</b> 🔴 &lt;5 ft · 🟠 5–10 ft · 🟡 10–20 ft · 🟢 &gt;20 ft.<br>
<b>⭐ = good deal:</b> USGS elevation ≥ {cfg.min_elevation_ft:g} ft, FEMA Zone X, Flood Factor ≤ {cfg.max_flood_factor},
opening bid ≤ {cfg.max_bid_ratio * 100:.0f}% of market value, no active city/county liens found, and a real address.<br>
<b>Sources:</b> RealTaxDeed (Pinellas Clerk auction calendar) · Pinellas County Property Appraiser · Pinellas County GIS ·
USGS Elevation Point Query · FEMA National Flood Hazard Layer · First Street Flood Factor ·
Pinellas Clerk Official Records (searched by current owner name — an automated screen, not a title search).<br>
The opening bid is only the starting price, and final bids often go much higher. This is research only, not legal or financial advice;
have a title attorney review any property before you bid.</p></div>"""
    return subject, html
