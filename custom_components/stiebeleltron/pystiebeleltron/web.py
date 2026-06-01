"""Stiebel Eltron ISGWeb HTTP client for cooling page settings."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

_LOGGED_IN_MARKERS = ("angemeldet", "logout")


# ---------------------------------------------------------------------------
# Register descriptors
# ---------------------------------------------------------------------------

@dataclass
class WebNumberRegister:
    name: str
    key: str        # e.g. "val377"
    unit: str
    min: float
    max: float
    step: float
    data_type: str  # "int" | "float" | "double"


@dataclass
class WebSwitchRegister:
    name: str
    key: str        # radio-button group, values 0/1


@dataclass
class WebSelectRegister:
    name: str
    key: str
    options: dict[str, str]  # value_str -> display label


# ---------------------------------------------------------------------------
# Page definitions
# ---------------------------------------------------------------------------

@dataclass
class WebPage:
    path: str   # e.g. "/?s=4,8,1"
    name: str   # human-readable, used in entity names
    registers: list  # list[WebNumberRegister | WebSwitchRegister | WebSelectRegister]


COOLING_PAGE_GRUNDEINSTELLUNG = WebPage(
    path="/?s=4,8,1",
    name="Grundeinstellung",
    registers=[
        WebNumberRegister("KÜHLSTUFEN",          "val377",   "",   1,  6,  1.0,  "int"),
        WebNumberRegister("GRENZE KÜHLEN",        "val457",   "°C", 15, 40, 0.1,  "float"),
        WebNumberRegister("LEISTUNG KÜHLEN",      "val1128",  "kW", 18, 20, 0.1,  "float"),
        WebNumberRegister("HYSTERESE VORLAUFTEMP","val11059", "K",  4,  10, 0.1,  "float"),
        WebNumberRegister("DYNAMIK AKTIV",        "val11060", "",   1,  10, 1.0,  "int"),
    ],
)

COOLING_PAGE_KUEHLKREIS2 = WebPage(
    path="/?s=4,8,2",
    name="Kühlkreis 2",
    registers=[
        WebSwitchRegister("KÜHLKREIS",         "val11068"),
        WebNumberRegister("RAUMSOLLTEMPERATUR","val11070", "°C", 20, 30, 0.1,  "float"),
        WebSelectRegister("KÜHLART",           "val11071", {"0": "GEBLÄSEKÜHLUNG", "1": "FLÄCHENKÜHLUNG"}),
        WebNumberRegister("STEIGUNG KÜHLKURVE","val12040", "",   0,  3,  0.05, "double"),
        WebNumberRegister("STARTTEMPERATUR",   "val12041", "°C", 9,  30, 0.5,  "float"),
    ],
)

COOLING_PAGE_KUEHLKREIS3 = WebPage(
    path="/?s=4,8,3",
    name="Kühlkreis 3",
    registers=[
        WebSwitchRegister("KÜHLKREIS",         "val11073"),
        WebNumberRegister("RAUMSOLLTEMPERATUR","val11075", "°C", 20, 30, 0.1,  "float"),
        WebSelectRegister("KÜHLART",           "val11076", {"0": "GEBLÄSEKÜHLUNG", "1": "FLÄCHENKÜHLUNG"}),
        WebNumberRegister("STEIGUNG KÜHLKURVE","val12043", "",   0,  3,  0.05, "double"),
        WebNumberRegister("STARTTEMPERATUR",   "val12044", "°C", 9,  30, 0.5,  "float"),
    ],
)

ALL_COOLING_PAGES: list[WebPage] = [
    COOLING_PAGE_GRUNDEINSTELLUNG,
    COOLING_PAGE_KUEHLKREIS2,
    COOLING_PAGE_KUEHLKREIS3,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_value(value: float, data_type: str) -> str:
    """Format a value as ISGWeb expects (comma decimal separator, European locale)."""
    if data_type == "int":
        return str(int(value))
    if data_type == "double":
        return f"{value:.2f}".replace(".", ",")
    return f"{value:.1f}".replace(".", ",")


def _is_logged_in(html: str) -> bool:
    lower = html.lower()
    return any(m in lower for m in _LOGGED_IN_MARKERS)


def _parse_page(html: str) -> tuple[dict[str, float | None], str | None]:
    """Parse all register values and the sessionToken from an ISGWeb page.

    Handles two value encoding styles:
      1. jsvalues['id']['val']='20,0'   – numeric text inputs
      2. Checked radio buttons           – switches and selects
    """
    data: dict[str, float | None] = {}

    # Numeric inputs (jsvalues JS array)
    for m in re.finditer(r"jsvalues\['(\d+)'\]\['val'\]='([^']*)'", html):
        key = "val" + m.group(1)
        raw = m.group(2).replace(",", ".")
        try:
            data[key] = float(raw)
        except ValueError:
            data[key] = None

    # Radio buttons: find all <input type="radio" ...> tags and pick the checked one
    for tag_m in re.finditer(r"<input[^>]+type=['\"]radio['\"][^>]*>", html, re.IGNORECASE):
        tag = tag_m.group(0)
        if "checked" not in tag:
            continue
        name_m = re.search(r'name="(val\d+)"', tag)
        value_m = re.search(r'value="(\d+)"', tag)
        if name_m and value_m:
            data[name_m.group(1)] = float(value_m.group(1))

    # Session token
    token_m = re.search(r'id="sessionToken"[^>]*>([^<]+)<', html)
    token = token_m.group(1).strip() if token_m else None

    return data, token


# ---------------------------------------------------------------------------
# API class
# ---------------------------------------------------------------------------

class WebStiebelEltronCoolingAPI:
    """ISGWeb HTTP client for reading/writing all three cooling pages."""

    def __init__(self, host: str, username: str, password: str) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._base_url = f"http://{host}"
        self._session: aiohttp.ClientSession | None = None
        self._data: dict[str, float | None] = {}
        self._session_tokens: dict[str, str | None] = {}  # page path -> token

        # Build reverse mapping: register key -> page (for writes)
        self._key_to_page: dict[str, WebPage] = {}
        for page in ALL_COOLING_PAGES:
            for reg in page.registers:
                self._key_to_page[reg.key] = page

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def connect(self) -> bool:
        """POST credentials to the portal root and verify authentication."""
        session = await self._ensure_session()
        try:
            resp = await session.post(
                self._base_url + "/",
                data={"username": self._username, "password": self._password},
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=10),
            )
            text = await resp.text()
            return resp.status == 200 and _is_logged_in(text)
        except aiohttp.ClientError as err:
            _LOGGER.error("Web portal auth error for %s: %s", self._host, err)
            return False

    async def _get_page_html(self, path: str) -> str | None:
        session = await self._ensure_session()
        try:
            resp = await session.get(
                self._base_url + path,
                timeout=aiohttp.ClientTimeout(total=10),
            )
            if resp.status != 200:
                _LOGGER.warning("HTTP %s from %s%s", resp.status, self._host, path)
                return None
            return await resp.text()
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to fetch %s%s: %s", self._host, path, err)
            raise

    async def async_update(self) -> None:
        """Fetch all cooling pages and merge the parsed values."""
        for page in ALL_COOLING_PAGES:
            html = await self._get_page_html(page.path)
            if html is None:
                continue

            # Re-authenticate on session expiry
            if not _is_logged_in(html):
                _LOGGER.debug("ISGWeb session expired for %s, re-authenticating", self._host)
                if not await self.connect():
                    _LOGGER.error("Re-authentication failed for %s", self._host)
                    continue
                html = await self._get_page_html(page.path)
                if html is None:
                    continue

            page_data, token = _parse_page(html)
            self._data.update(page_data)
            self._session_tokens[page.path] = token

    def get_register_value(self, key: str) -> float | None:
        return self._data.get(key)

    def has_register_value(self, key: str) -> bool:
        return key in self._data and self._data[key] is not None

    async def write_register_value(self, key: str, value: float) -> None:
        """Write a register value back to the appropriate ISGWeb page."""
        page = self._key_to_page.get(key)
        if page is None:
            raise ValueError(f"Unknown cooling register key: {key}")

        post_data: dict[str, str] = {}
        token = self._session_tokens.get(page.path)
        if token:
            post_data["sessionToken"] = token

        for reg in page.registers:
            current = self._data.get(reg.key)
            if current is None:
                continue
            send = value if reg.key == key else current
            if isinstance(reg, WebNumberRegister):
                post_data[reg.key] = _format_value(send, reg.data_type)
            else:
                # switch or select: always integer
                post_data[reg.key] = str(int(send))

        session = await self._ensure_session()
        resp = await session.post(
            self._base_url + page.path,
            data=post_data,
            timeout=aiohttp.ClientTimeout(total=10),
        )
        if resp.status != 200:
            raise ValueError(f"Write to {page.path} returned HTTP {resp.status}")

        # Update local cache immediately
        reg = next(r for r in page.registers if r.key == key)
        if isinstance(reg, WebNumberRegister) and reg.data_type == "int":
            self._data[key] = float(int(value))
        else:
            self._data[key] = value
