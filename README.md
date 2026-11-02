# pinellas-tax-lien-finder

A daily email report of upcoming **Pinellas County, FL tax deed auctions** near an address you choose. It screens each property for flood risk and liens and stars (⭐) the good deals.

## What it does

1. **Auctions:** pulls every upcoming tax deed auction from [RealTaxDeed](https://pinellas.realtaxdeed.com), the Pinellas Clerk's auction site.
2. **Distance:** keeps only properties within `RADIUS_MILES` of `HOME_ADDRESS`, using parcel locations from Pinellas County GIS.
3. **Market value:** gets the just/market value from the [Pinellas County Property Appraiser](https://www.pcpao.gov).
4. **Flood risk:** checks three sources:
   - **USGS elevation** from the Elevation Point Query Service
   - **FEMA flood zone** from the National Flood Hazard Layer
   - **First Street Flood Factor** (1–10), formerly riskfactor.com
5. **Liens:** searches [Pinellas Clerk Official Records](https://officialrecords.mypinellasclerk.gov) by the current owner's name. It flags:
   - city or county liens, which can survive a Florida tax deed sale
   - State of Florida liens
   - mortgages, lis pendens and judgments, which are usually wiped out by the sale
6. **Email:** sends an HTML report that opens with what changed since the last run.

### ⭐ = good deal (all must be true)

| Rule | Default | Setting |
|---|---|---|
| USGS elevation | ≥ 20 ft | `MIN_ELEVATION_FT` |
| FEMA zone | X | — |
| First Street Flood Factor | ≤ 1 | `MAX_FLOOD_FACTOR` |
| Opening bid / market value | ≤ 75% | `MAX_BID_RATIO` |
| Active city/county liens found | 0 | — |
| Has a street address | yes | — |

## Privacy

The home address is **never committed**:

- **Locally**, it lives in `.env`, which is excluded from git.
- **In GitHub Actions**, it's a repository secret.

The script never logs the home location, the distances, or which properties are in range, because Actions logs on a public repo are public.

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then edit .env
python -m taxlien.main --no-email # writes out/report.html
python -m taxlien.main            # builds and emails it
```

## Run it daily on GitHub Actions

The workflow in `.github/workflows/daily-report.yml` runs every day at 23:32 UTC (5:32 PM Central in winter, 6:32 PM in summer). You can also start it by hand from the **Actions** tab.

Add these under **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Example |
|---|---|
| `HOME_ADDRESS` | `123 Example St N, St Petersburg, FL 33713` |
| `EMAIL_TO` | `you@gmail.com` (comma-separate for more than one) |
| `GMAIL_USER` | `you@gmail.com` |
| `GMAIL_APP_PASSWORD` | a [Google app password](https://myaccount.google.com/apppasswords); requires 2-Step Verification |

Optionally, override `RADIUS_MILES`, `MIN_ELEVATION_FT`, `MAX_BID_RATIO` or `MAX_FLOOD_FACTOR` under **Variables**.

"What's new" compares each run against the previous one, which is stored in the Actions cache (not in the repo).

## Caveats

- The lien check is an **automated screen by owner name, not a title search**. It can miss liens recorded under other names, and it can count releases that belong to other parcels the same owner holds.
- The City of St. Petersburg's official lien letter is a paid request, so city liens are found through Clerk records instead.
- The opening bid is only the minimum. Final bids are often much higher.
- This is research only, not legal or financial advice. Have a title attorney review any property before you bid.
