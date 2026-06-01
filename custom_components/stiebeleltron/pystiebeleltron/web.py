"""Stiebel Eltron ISGWeb HTTP client for cooling page settings."""
from __future__ import annotations

import json
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


@dataclass
class WebInfoPage:
    path: str        # e.g. "/?s=1,2"
    name: str        # human-readable, used in entity names
    key_prefix: str  # e.g. "wp1" — namespaces keys across heat pump pages


INFO_PAGES: list[WebInfoPage] = [
    WebInfoPage("/?s=1,2", "Wärmepumpe 1", "wp1"),
    WebInfoPage("/?s=1,3", "Wärmepumpe 2", "wp2"),
    WebInfoPage("/?s=1,4", "Wärmepumpe 3", "wp3"),
    WebInfoPage("/?s=1,5", "Wärmepumpe 4", "wp4"),
    WebInfoPage("/?s=1,6", "Wärmepumpe 5", "wp5"),
    WebInfoPage("/?s=1,7", "Wärmepumpe 6", "wp6"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_value_unit(raw: str) -> tuple[float | None, str]:
    """Split an ISGWeb display value like '16,1°C' into (16.1, '°C')."""
    m = re.match(r'^([+-]?\d[\d,.]*)\s*(.*)', raw.strip())
    if not m:
        return None, ""
    num_str = m.group(1).replace(",", ".")
    unit = m.group(2).strip()
    # Normalise non-standard unit spellings
    if unit == "KWh":
        unit = "kWh"
    try:
        return float(num_str), unit
    except ValueError:
        return None, unit


def _parse_info_page(html: str, prefix: str) -> tuple[dict[str, float | None], dict[str, str]]:
    """Parse a read-only HTML info table into (data, units) dicts keyed by '{prefix}:{label}'."""
    data: dict[str, float | None] = {}
    units: dict[str, str] = {}
    pattern = re.compile(
        r'<td[^>]*class=["\']key["\'][^>]*>\s*([^<]+?)\s*</td>'
        r'\s*<td[^>]*class=["\']value["\'][^>]*>\s*([^<]*?)\s*</td>',
        re.IGNORECASE,
    )
    for m in pattern.finditer(html):
        label = m.group(1).strip()
        raw = m.group(2).strip()
        key = f"{prefix}:{label}"
        value, unit = _split_value_unit(raw)
        data[key] = value
        units[key] = unit
    return data, units


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
        self._info_units: dict[str, str] = {}  # key -> unit string for info pages
        self._session_tokens: dict[str, str | None] = {}  # page path -> token

        # Build reverse mapping: register key -> page (for writes)
        self._key_to_page: dict[str, WebPage] = {}
        for page in ALL_COOLING_PAGES:
            for reg in page.registers:
                self._key_to_page[reg.key] = page

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # unsafe=True is required to accept cookies from plain IP addresses
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True)
            )
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
                data={"make": "send", "user": self._username, "pass": self._password},
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=60),
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
                timeout=aiohttp.ClientTimeout(total=60),
            )
            if resp.status != 200:
                _LOGGER.warning("HTTP %s from %s%s", resp.status, self._host, path)
                return None
            return await resp.text()
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to fetch %s%s: %s", self._host, path, err)
            raise

    async def async_update(self) -> None:
        """Fetch all cooling and info pages and merge the parsed values."""
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

        for info_page in INFO_PAGES:
            html = await self._get_page_html(info_page.path)
            if html is None:
                continue

            if not _is_logged_in(html):
                _LOGGER.debug("ISGWeb session expired for %s, re-authenticating", self._host)
                if not await self.connect():
                    _LOGGER.error("Re-authentication failed for %s", self._host)
                    continue
                html = await self._get_page_html(info_page.path)
                if html is None:
                    continue

            page_data, page_units = _parse_info_page(html, info_page.key_prefix)
            self._data.update(page_data)
            self._info_units.update(page_units)

    def get_register_value(self, key: str) -> float | None:
        return self._data.get(key)

    def has_register_value(self, key: str) -> bool:
        return key in self._data and self._data[key] is not None

    def get_info_unit(self, key: str) -> str:
        return self._info_units.get(key, "")

    def get_info_page_keys(self, prefix: str) -> list[str]:
        """Return all data keys belonging to a given info page prefix."""
        return [k for k in self._data if k.startswith(f"{prefix}:")]

    async def write_register_value(self, key: str, value: float) -> None:
        """Write a register value to the ISGWeb via save.php.

        save.php expects a form field 'data' whose value is a JSON array:
        [{"name": "val11068", "value": "1"}, ...]
        All fields from the same page are sent together.
        """
        page = self._key_to_page.get(key)
        if page is None:
            raise ValueError(f"Unknown cooling register key: {key}")

        payload = []
        for reg in page.registers:
            current = self._data.get(reg.key)
            if current is None:
                continue
            send = value if reg.key == key else current
            if isinstance(reg, WebNumberRegister):
                str_val = _format_value(send, reg.data_type)
            else:
                str_val = str(int(send))
            payload.append({"name": reg.key, "value": str_val})

        session = await self._ensure_session()
        resp = await session.post(
            self._base_url + "/save.php",
            data={"data": json.dumps(payload, separators=(",", ":"))},
            timeout=aiohttp.ClientTimeout(total=60),
        )
        if resp.status != 200:
            raise ValueError(f"save.php returned HTTP {resp.status}")

        result = await resp.json(content_type=None)
        if not result.get("success"):
            raise ValueError(f"save.php error: {result.get('message', 'unknown')}")

        # Update local cache so native_value reflects the change immediately
        reg = next(r for r in page.registers if r.key == key)
        if isinstance(reg, WebNumberRegister) and reg.data_type == "int":
            self._data[key] = float(int(value))
        else:
            self._data[key] = value
