import logging
import time
from urllib.parse import urlparse

import requests

_logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "1.51.0"
DEFAULT_API_URL = "https://api.navirec.com/"
DEFAULT_USER_AGENT = "TajOdoo/1.0.0 (fleet_navirec)"


class NavirecAPIError(Exception):
    pass


class NavirecClient:
    def __init__(self, api_url=DEFAULT_API_URL, token=None, version=None,
                 user_agent=DEFAULT_USER_AGENT, timezone=None):
        self.api_url = (api_url or DEFAULT_API_URL).rstrip("/") + "/"
        self.token = token or ""
        self.version = version or DEFAULT_API_VERSION
        self.user_agent = user_agent
        self.timezone = timezone

    @property
    def headers(self):
        headers = {
            "Authorization": f"Token {self.token}",
            "Accept": f"application/json; version={self.version}",
            "User-Agent": self.user_agent,
        }
        if self.timezone:
            headers["Accept-Timezone"] = self.timezone
        return headers

    def _request(self, method, path, params=None):
        url = path if path.startswith("http") else self.api_url + path.lstrip("/")
        last_response = None
        for attempt in range(2):
            try:
                response = requests.request(
                    method, url, headers=self.headers, params=params, timeout=30
                )
                last_response = response
            except requests.RequestException as exc:
                raise NavirecAPIError(f"Navirec network error: {exc}") from exc

            if response.status_code == 429 and attempt == 0:
                try:
                    retry_after = float(
                        response.headers.get("Retry-After", "1") or 1
                    )
                except (TypeError, ValueError):
                    retry_after = 1.0
                time.sleep(max(retry_after, 0))
                continue

            if response.status_code in (401, 403):
                raise NavirecAPIError("Invalid token or missing permissions")
            if response.status_code == 429:
                raise NavirecAPIError("Navirec rate limit exceeded after retry")
            if response.status_code >= 500:
                raise NavirecAPIError(
                    f"Navirec server error {response.status_code}: {response.text[:200]}"
                )
            if response.status_code >= 400:
                raise NavirecAPIError(
                    f"Navirec API error {response.status_code}: {response.text[:200]}"
                )
            try:
                response.json()
            except ValueError as exc:
                raise NavirecAPIError("Navirec returned invalid JSON") from exc
            return response

        raise NavirecAPIError(
            f"Navirec request failed: {getattr(last_response, 'status_code', 'unknown')}"
        )

    def get_vehicles(self):
        vehicles = []
        next_url = self.api_url + "vehicles/"
        params = {"ordering": "id", "page_size": 1000}
        while next_url:
            response = self._request("GET", next_url, params=params)
            data = response.json()
            if isinstance(data, list):
                vehicles.extend(data)
            elif isinstance(data, dict):
                vehicles.extend(data.get("results", []))
            else:
                raise NavirecAPIError("Unexpected vehicles payload")
            next_url = response.links.get("next", {}).get("url")
            params = None
        return vehicles

    def get_last_vehicle_states(self, account_id=None, vehicle_id=None):
        states = []
        next_url = self.api_url + "last_vehicle_states/"
        params = {"page_size": 1000}
        if account_id:
            params["account"] = account_id
        if vehicle_id:
            params["vehicle"] = vehicle_id
        while next_url:
            response = self._request("GET", next_url, params=params)
            data = response.json()
            if isinstance(data, list):
                states.extend(data)
            elif isinstance(data, dict):
                states.extend(data.get("results", []))
            else:
                raise NavirecAPIError("Unexpected vehicle state payload")
            next_url = response.links.get("next", {}).get("url")
            params = None
        return states

    def test_connection(self):
        response = self._request("GET", "vehicles/", params={"page_size": 1})
        response.json()
        return True


def vehicle_uuid_from_url(url):
    if not url:
        return False
    path = urlparse(url).path.rstrip("/")
    value = path.split("/")[-1] if path else ""
    return value or False
