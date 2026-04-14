#!/usr/bin/env python3

"""Load browser-exported cookies into a requests.Session for wattpad.com."""

from __future__ import annotations

import json
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests
from requests.cookies import create_cookie


def _set_cookie(
    session: requests.Session,
    name: str,
    value: str,
    domain: str,
    path: str,
) -> None:
    domain = domain.strip()
    if not domain.startswith(".") and "wattpad.com" in domain.lower():
        host = domain.lower().split(":")[0]
        if host in {"www.wattpad.com", "wattpad.com"}:
            domain = ".wattpad.com"
    cookie = create_cookie(name, value, domain=domain, path=path or "/")
    session.cookies.set_cookie(cookie)


def _load_json_cookies(session: requests.Session, path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        raw = raw["cookies"]
    if not isinstance(raw, list):
        raise ValueError("Cookie JSON must be a list of cookie objects, or {\"cookies\": [...]}.")

    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        domain = (item.get("domain") or ".wattpad.com").strip()
        path_s = (item.get("path") or "/").strip() or "/"
        _set_cookie(session, str(name), str(value), domain, path_s)


def _load_netscape_cookies(session: requests.Session, path: Path) -> None:
    jar = MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    for c in jar:
        session.cookies.set_cookie(c)


def load_wattpad_cookies(session: requests.Session, path: str | Path) -> None:
    """Merge cookies from a Netscape cookies.txt or JSON (Cookie-Editor style) file."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Cookie file not found: {p}")

    suffix = p.suffix.lower()
    if suffix == ".json":
        _load_json_cookies(session, p)
        return

    try:
        _load_netscape_cookies(session, p)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Could not read cookies as Netscape format from {p}. "
            "Use a cookies.txt export for wattpad.com, or a JSON list export."
        ) from exc
