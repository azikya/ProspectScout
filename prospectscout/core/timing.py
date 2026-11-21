"""Where is the prospect, and when should each message go out?

Timezone comes from, in order: their phone number, the address Claude read on their
contact page, then the country-code domain. Send windows are business-hours slots on
Tue–Thu (never weekends, which also avoids Monday mornings and Friday afternoons).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_WINDOWS: dict = {
    "email": [["08:00", "10:00"]],
    "linkedin": [["07:30", "09:00"], ["17:00", "18:00"]],
    "whatsapp": [["10:00", "12:00"], ["15:00", "17:00"]],
}
SEND_DAYS = (1, 2, 3)  # Tue, Wed, Thu
MIN_SLOT = timedelta(minutes=30)  # don't suggest a window that's about to close
# Gulf states with a Friday+Saturday weekend (the UAE moved to Saturday+Sunday in 2022).
FRI_SAT_WEEKEND = frozenset({"Asia/Riyadh", "Asia/Qatar", "Asia/Kuwait", "Asia/Bahrain", "Asia/Muscat", "Asia/Aden",
                             "Asia/Baghdad"})

COUNTRY_TZ = {
    "usa": "America/New_York", "us": "America/New_York", "united states": "America/New_York",
    "united states of america": "America/New_York", "uk": "Europe/London", "united kingdom": "Europe/London",
    "england": "Europe/London", "great britain": "Europe/London", "uae": "Asia/Dubai",
    "united arab emirates": "Asia/Dubai", "saudi arabia": "Asia/Riyadh", "ksa": "Asia/Riyadh", "qatar": "Asia/Qatar",
    "australia": "Australia/Sydney", "canada": "America/Toronto", "india": "Asia/Kolkata", "germany": "Europe/Berlin",
    "france": "Europe/Paris", "spain": "Europe/Madrid", "italy": "Europe/Rome", "netherlands": "Europe/Amsterdam",
    "ireland": "Europe/Dublin", "singapore": "Asia/Singapore", "new zealand": "Pacific/Auckland",
    "switzerland": "Europe/Zurich", "sweden": "Europe/Stockholm", "norway": "Europe/Oslo", "denmark": "Europe/Copenhagen",
    "belgium": "Europe/Brussels", "austria": "Europe/Vienna", "poland": "Europe/Warsaw", "portugal": "Europe/Lisbon",
}
CCTLD_TZ = {
    "uk": "Europe/London", "de": "Europe/Berlin", "fr": "Europe/Paris", "es": "Europe/Madrid", "it": "Europe/Rome",
    "nl": "Europe/Amsterdam", "ie": "Europe/Dublin", "ae": "Asia/Dubai", "sa": "Asia/Riyadh", "qa": "Asia/Qatar",
    "au": "Australia/Sydney", "ca": "America/Toronto", "in": "Asia/Kolkata", "sg": "Asia/Singapore",
    "nz": "Pacific/Auckland", "ch": "Europe/Zurich", "se": "Europe/Stockholm", "no": "Europe/Oslo",
    "dk": "Europe/Copenhagen", "be": "Europe/Brussels", "at": "Europe/Vienna", "pl": "Europe/Warsaw", "pt": "Europe/Lisbon",
}


def _valid_zone(name: str | None) -> str | None:
    if not name:
        return None
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _tz_from_phone(phone: str | None) -> str | None:
    s = (phone or "").strip()
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        return None
    try:
        import phonenumbers
        from phonenumbers import timezone as pn_tz

        num = phonenumbers.parse(s, None)
        if not phonenumbers.is_possible_number(num):
            return None
        zones = [z for z in pn_tz.time_zones_for_number(num) if z != "Etc/Unknown"]
    except Exception:  # noqa: BLE001 - any parse problem simply means "unknown"
        return None
    return _valid_zone(zones[0]) if zones else None


def resolve_timezone(phone: str | None, tz_hint: dict | None, domain: str | None) -> tuple[str | None, str]:
    tz = _tz_from_phone(phone)
    if tz:
        return tz, "phone"
    hint = tz_hint or {}
    tz = _valid_zone(hint.get("iana")) or _valid_zone(COUNTRY_TZ.get((hint.get("country") or "").strip().lower()))
    if tz:
        return tz, "address"
    tld = (domain or "").lower().rstrip(".").rsplit(".", 1)[-1]
    if tld in CCTLD_TZ:
        return CCTLD_TZ[tld], "domain"
    return None, "unknown"


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _clock(dt: datetime, with_suffix: bool = True) -> str:
    h = dt.hour % 12 or 12
    out = f"{h}:{dt.minute:02d}"
    return out + (("am" if dt.hour < 12 else "pm") if with_suffix else "")


def _range(a: datetime, b: datetime) -> str:
    same = (a.hour < 12) == (b.hour < 12)
    return f"{_clock(a, not same)}–{_clock(b)}"


def _user_zone(user_tz: str | None):
    z = _valid_zone(user_tz)
    return ZoneInfo(z) if z else datetime.now().astimezone().tzinfo


def next_slots(iana: str, now_utc: datetime, windows: dict = DEFAULT_WINDOWS, user_tz: str | None = None) -> dict:
    tz = ZoneInfo(iana)
    local_now = now_utc.astimezone(tz).replace(second=0, microsecond=0)
    uz = _user_zone(user_tz)
    out: dict = {}
    for channel, wins in (windows or DEFAULT_WINDOWS).items():
        spans = sorted((_hm(a), _hm(b)) for a, b in wins)
        found = None
        for d in range(0, 15):
            day = local_now.date() + timedelta(days=d)
            if day.weekday() not in SEND_DAYS:
                continue
            for a, b in spans:
                start = datetime.combine(day, a, tz)
                end = datetime.combine(day, b, tz)
                if end - max(start, local_now) < MIN_SLOT:
                    continue
                found = (max(start, local_now), end)
                break
            if found:
                break
        if not found:
            continue
        start, end = found
        us, ue = start.astimezone(uz), end.astimezone(uz)
        user_day = "" if us.date() == start.date() else f"{us:%a} "
        out[channel] = {
            "start": start.isoformat(), "end": end.isoformat(), "start_user": us.isoformat(),
            "label": f"{start:%a} {_range(start, end)} their time · {user_day}{_range(us, ue)} your time",
        }
    return out


def local_now_label(iana: str, now_utc: datetime) -> str:
    return f"{_clock(now_utc.astimezone(ZoneInfo(iana)))} there now"


def weekend_for(tz: str | None) -> tuple[int, int]:
    return (4, 5) if tz in FRI_SAT_WEEKEND else (5, 6)


def _add_business_days(d: date, n: int, weekend: tuple[int, int] = (5, 6)) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() not in weekend:
            n -= 1
    return d


def followup_dates(sent_on: date, spacing=((2, 3), (3, 5), (4, 7)), tz: str | None = None) -> list[tuple[date, date]]:
    out, anchor, weekend = [], sent_on, weekend_for(tz)
    for lo, hi in spacing:
        rng = (_add_business_days(anchor, int(lo), weekend), _add_business_days(anchor, int(hi), weekend))
        out.append(rng)
        anchor = rng[0]
    return out
