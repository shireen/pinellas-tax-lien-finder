"""Settings, read from environment variables (or a local .env file that is never committed)."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


@dataclass
class Config:
    home_address: str
    radius_miles: float = 5.0
    min_elevation_ft: float = 20.0
    max_bid_ratio: float = 0.75
    max_flood_factor: int = 1
    months_ahead: int = 3
    email_to: list = field(default_factory=list)
    gmail_user: str = ""
    gmail_app_password: str = ""
    state_file: Path = Path("state/last_run.json")
    output_html: Path = Path("out/report.html")

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        home = os.environ.get("HOME_ADDRESS", "").strip()
        if not home:
            raise SystemExit("HOME_ADDRESS is not set (put it in .env locally or a GitHub Actions secret).")
        to = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]
        return cls(
            home_address=home,
            radius_miles=float(os.environ.get("RADIUS_MILES", 5)),
            min_elevation_ft=float(os.environ.get("MIN_ELEVATION_FT", 20)),
            max_bid_ratio=float(os.environ.get("MAX_BID_RATIO", 0.75)),
            max_flood_factor=int(os.environ.get("MAX_FLOOD_FACTOR", 1)),
            months_ahead=int(os.environ.get("MONTHS_AHEAD", 3)),
            email_to=to,
            gmail_user=os.environ.get("GMAIL_USER", ""),
            gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
        )
