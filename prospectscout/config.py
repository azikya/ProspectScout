"""Local settings and company profile.

Stored as JSON in the data dir (``~/.prospectscout/config.json`` by default, or
``$PROSPECTSCOUT_HOME``). Nothing secret is ever hardcoded; the defaults below are
Azikya's public company profile, which the user confirms or edits in Settings.
"""

from __future__ import annotations

import copy
import json
import os
import threading

ENV_HOME = "PROSPECTSCOUT_HOME"
_LOCK = threading.RLock()  # serializes load-modify-save so concurrent settings saves can't collide

DEFAULTS: dict = {
    "company": {
        "name": "Azikya Software Solutions",
        "short_name": "Azikya",
        "website": "https://azikya.com",
        "logo": "/brand/logo.png",
        "pitch": "Custom software, Salesforce, web and mobile development partner",
        "services": [
            "Salesforce consulting (Sales Cloud, Service Cloud, Marketing Cloud, Commerce Cloud, "
            "Field Service, CPQ, Pardot, Heroku)",
            "Custom software development",
            "Web development",
            "CMS & ERP development",
            "Mobile app development",
            "Web design, UI/UX & branding",
            "AI & automation (chatbots, workflow automation, integrations)",
        ],
        "industries": [
            "Banking & Finance", "Fintech", "Manufacturing", "Healthcare & Pharma",
            "Education", "Real Estate", "Recruitment & Staffing", "Tourism & Hospitality",
        ],
        "markets": ["US", "Australia", "Europe"],
        "case_studies": ["Age Up Health", "Lead Tailor", "Luna", "Top Step"],
    },
    "sender": {"name": "", "title": "", "email": "", "phone": "", "booking_link": "", "signature": ""},
    "user_timezone": "",
    # Literal copy of timing.DEFAULT_WINDOWS (a test keeps them equal).
    "send_windows": {
        "email": [["08:00", "10:00"]],
        "linkedin": [["07:30", "09:00"], ["17:00", "18:00"]],
        "whatsapp": [["10:00", "12:00"], ["15:00", "17:00"]],
    },
    "followup_spacing": [[2, 3], [3, 5], [4, 7]],  # business days after the previous touch (SOP)
    "model": "",
    "pagespeed": {"enabled": False, "api_key": ""},
    "max_pages": 8,
    "max_link_checks": 40,
    "concurrency": 1,
    "sop": {
        "industries": [
            "HealthTech", "Fintech", "Financial Services", "Insurance", "Retail", "E-commerce",
            "Telecom", "Manufacturing", "Logistics", "Supply Chain", "Travel", "Hospitality",
        ],
        "countries": ["USA", "UK", "Europe", "UAE", "Saudi Arabia", "Qatar", "Australia", "Canada"],
        "size_min": 11,
        "size_max": 1000,
        "weights": {
            "changed_job_recently": 2,
            "active_on_linkedin": 1,
            "talks_tech_growth": 2,
            "hiring_tech_roles": 3,
            "funding_news": 3,
            "expansion_launch": 2,
        },
    },
}


def home_dir() -> str:
    return os.environ.get(ENV_HOME) or os.path.join(os.path.expanduser("~"), ".prospectscout")


def config_path() -> str:
    return os.path.join(home_dir(), "config.json")


def _deep_merge(base: dict, patch: dict) -> dict:
    """Return base updated recursively with patch (dicts merge, everything else replaces)."""
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = config_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                _deep_merge(cfg, json.load(fh) or {})
        except (OSError, ValueError):
            pass
    return cfg


def save(cfg: dict) -> str:
    path = config_path()
    with _LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)  # owner-only on POSIX; no-op on Windows
    except OSError:
        pass
    return path


def update(patch: dict) -> dict:
    with _LOCK:
        cfg = _deep_merge(load(), patch)
        save(cfg)
    return cfg
