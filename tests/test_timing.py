from datetime import date, datetime, timezone

from prospectscout import config
from prospectscout.core import timing

SAT = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)  # Saturday


def test_defaults_match_config():
    assert config.DEFAULTS["send_windows"] == timing.DEFAULT_WINDOWS


def test_tz_from_phone():
    assert timing.resolve_timezone("+44 20 7946 0958", None, None) == ("Europe/London", "phone")


def test_tz_from_00_prefixed_phone():
    assert timing.resolve_timezone("0044 20 7946 0958", None, None) == ("Europe/London", "phone")


def test_tz_from_hint():
    assert timing.resolve_timezone(None, {"iana": "Asia/Dubai"}, None) == ("Asia/Dubai", "address")


def test_invalid_iana_hint_falls_back_to_country():
    assert timing.resolve_timezone(None, {"iana": "Mars/Base", "country": "Qatar"}, None) == ("Asia/Qatar", "address")


def test_tz_from_country_hint_aliases():
    assert timing.resolve_timezone(None, {"country": "Qatar"}, None) == ("Asia/Qatar", "address")
    assert timing.resolve_timezone(None, {"country": "United Kingdom"}, None) == ("Europe/London", "address")


def test_tz_from_cctld():
    assert timing.resolve_timezone(None, None, "acme.com.au") == ("Australia/Sydney", "domain")
    assert timing.resolve_timezone(None, None, "acme.co.uk") == ("Europe/London", "domain")


def test_tz_unknown():
    assert timing.resolve_timezone("12345", None, "acme.com") == (None, "unknown")


def test_skips_weekend():
    s = timing.next_slots("America/Chicago", SAT)["email"]
    assert s["start"].startswith("2026-11-24T08:00")


def test_inside_window_starts_now():
    now = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)  # 08:30 Chicago, Tue
    assert timing.next_slots("America/Chicago", now)["email"]["start"].startswith("2026-11-24T08:30")


def test_after_last_window_moves_to_next_send_day():
    now = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)  # Thu 18:00 Chicago -> after LinkedIn 17-18
    assert timing.next_slots("America/Chicago", now)["linkedin"]["start"].startswith("2026-12-01T07:30")


def test_user_tz_conversion():
    s = timing.next_slots("America/Chicago", SAT, user_tz="Asia/Kolkata")["email"]
    assert s["start_user"].startswith("2026-11-24T19:30") and "your time" in s["label"]
    assert s["label"].startswith("Tue 8:00–10:00am their time")


def test_all_channels_present():
    assert set(timing.next_slots("Europe/London", SAT)) == {"email", "linkedin", "whatsapp"}


def test_local_now_label():
    assert timing.local_now_label("America/Chicago", SAT) == "6:00am there now"


def test_followups_business_days():
    d = timing.followup_dates(date(2026, 11, 20))
    assert d[0] == (date(2026, 11, 24), date(2026, 11, 25)) and len(d) == 3
    assert all(x[0].weekday() < 5 and x[1].weekday() < 5 for x in d)


# --- deferred minors -----------------------------------------------------------------------

def test_slot_with_too_little_time_left_moves_to_next_window():
    now = datetime(2026, 11, 24, 15, 50, tzinfo=timezone.utc)  # 09:50 Chicago, 10 min left in 08:00-10:00
    assert timing.next_slots("America/Chicago", now)["email"]["start"].startswith("2026-11-25T08:00")


def test_gulf_weekend_for_followups():
    # Saudi weekend is Fri+Sat: sent Thu 2026-11-19, 2 business days later is Mon 2026-11-23 (Sun counts as a workday)
    assert timing.followup_dates(date(2026, 11, 19), tz="Asia/Riyadh")[0][0] == date(2026, 11, 23)
    # UAE moved to a Sat+Sun weekend in 2022
    assert timing.followup_dates(date(2026, 11, 19), tz="Asia/Dubai")[0][0] == date(2026, 11, 23)
    assert timing.followup_dates(date(2026, 11, 19), tz="Asia/Riyadh")[0][1] == date(2026, 11, 24)


def test_custom_followup_spacing():
    d = timing.followup_dates(date(2026, 11, 20), spacing=((1, 1), (1, 1)))
    assert d == [(date(2026, 11, 23), date(2026, 11, 23)), (date(2026, 11, 24), date(2026, 11, 24))]
