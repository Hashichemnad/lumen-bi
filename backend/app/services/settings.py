"""Organisation settings and personal preferences.

Regional settings (admin): default theme and language, how dates are shown (Gregorian, Hijri or both), the
first day of the week, the weekend and the first month of the fiscal year. The query engine uses the week
start and fiscal year for date levels and relative date filters; the browser uses the rest.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.models import AppSetting, User

Theme = Literal["classic", "advanced", "analyst"]
Language = Literal["en", "ar"]
CalendarDisplay = Literal["gregorian", "hijri", "both"]


class RegionalSettings(BaseModel):
    default_theme: Theme = "classic"
    default_language: Language = "en"
    calendar: CalendarDisplay = "gregorian"
    # ISO weekday numbers: 1 = Monday … 7 = Sunday. UAE: the week starts on Monday, Saturday and Sunday off
    week_start: int = Field(1, ge=1, le=7)
    weekend: list[int] = [6, 7]
    fiscal_year_start: int = Field(1, ge=1, le=12)  # month the fiscal year starts in

    @field_validator("weekend")
    @classmethod
    def _weekend(cls, v: list[int]) -> list[int]:
        days = sorted(set(v))
        if any(d < 1 or d > 7 for d in days) or len(days) > 3:
            raise ValueError("The weekend is up to three days, numbered 1 (Monday) to 7 (Sunday)")
        return days


class Preferences(BaseModel):
    theme: Theme | None = None
    language: Language | None = None
    calendar: CalendarDisplay | None = None


def regional(db: Session) -> RegionalSettings:
    row = db.get(AppSetting, "regional")
    try:
        return RegionalSettings(**(row.value if row else {}))
    except ValueError:
        # A stored value that no longer validates falls back to the defaults rather than breaking queries
        return RegionalSettings()


def save_regional(db: Session, value: RegionalSettings, user: User) -> RegionalSettings:
    row = db.get(AppSetting, "regional")
    if row is None:
        row = AppSetting(key="regional", value={})
        db.add(row)
    row.value = value.model_dump()
    row.updated_by_id = user.id
    return value


def effective(db: Session, user: User) -> dict:
    """The theme, language and calendar a user sees: their own choice, else the organisation's default."""
    org = regional(db)
    prefs = Preferences(
        **{k: v for k, v in (user.preferences or {}).items() if k in Preferences.model_fields}
    )
    return {
        "theme": prefs.theme or org.default_theme,
        "language": prefs.language or org.default_language,
        "calendar": prefs.calendar or org.calendar,
    }
