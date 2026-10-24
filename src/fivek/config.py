from __future__ import annotations

import os
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def local_today() -> date:
    try:
        return datetime.now(ZoneInfo(os.getenv("FIVEK_TIMEZONE", "Europe/London"))).date()
    except ZoneInfoNotFoundError:
        raise ValueError("FIVEK_TIMEZONE must be a valid IANA time zone, for example Europe/London") from None


def load_environment(path: Path = Path(".env")) -> None:
    """Load simple KEY=value entries without logging or overriding process values."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data/fivek")
    target: date = date(2027, 1, 1)
    start: date | None = None
    request_delay: float = 0.25
    age: int = 30
    height_cm: float = 175.0
    sex: str = "male"

    def report_settings(self) -> dict:
        return {"target_date": self.target.isoformat(), "history_start": self.start.isoformat() if self.start else None,
                "time_zone": os.getenv("FIVEK_TIMEZONE", "Europe/London"),
                "age": self.age, "height_cm": self.height_cm, "sex": self.sex}

    @classmethod
    def environment(cls) -> "Config":
        delay = float(os.getenv("FIVEK_REQUEST_DELAY", "0.25"))
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("FIVEK_REQUEST_DELAY must be a finite number between 0 and 60 seconds")
        config = cls(
            data_dir=Path(os.getenv("FIVEK_DATA_DIR", "data/fivek")),
            target=date.fromisoformat(os.getenv("FIVEK_TARGET_DATE", "2027-01-01")),
            start=date.fromisoformat(os.environ["FIVEK_HISTORY_START"]) if os.getenv("FIVEK_HISTORY_START") else None,
            request_delay=delay,
            age=int(os.getenv("FIVEK_AGE", "30")),
            height_cm=float(os.getenv("FIVEK_HEIGHT_CM", "175")),
            sex=os.getenv("FIVEK_SEX", "male"),
        )
        if config.start and config.start > local_today():
            raise ValueError("History start must not be after today")
        if (config.target - local_today()).days > 730:
            raise ValueError("FIVEK_TARGET_DATE must be within the next two years")
        return config
