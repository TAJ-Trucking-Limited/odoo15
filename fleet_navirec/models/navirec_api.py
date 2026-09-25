import logging
import re
import time
from urllib.parse import urlparse

import requests

_logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "1.51.0"
DEFAULT_API_URL = "https://api.navirec.com/"
DEFAULT_GEOCODING_URL = "https://realtime.navirec.com/geocoding/"
# ponytail: pinned to the web release app.navirec.com sends to /configuration/.
# Ceiling: Navirec retires that release. Upgrade path: omit version, then the
# release string from the current web bundle.
DEFAULT_WEB_APP_VERSION = "2026.09.24"
DEFAULT_USER_AGENT = "TajOdoo/1.0.0 (fleet_navirec)"


class NavirecAPIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


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
                raise NavirecAPIError(
                    "Invalid token or missing permissions for %s (%s)"
                    % (urlparse(url).path or url, response.status_code),
                    status_code=response.status_code,
                )
            if response.status_code == 429:
                raise NavirecAPIError(
                    "Navirec rate limit exceeded after retry",
                    status_code=response.status_code,
                )
            if response.status_code >= 500:
                raise NavirecAPIError(
                    f"Navirec server error {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                )
            if response.status_code >= 400:
                raise NavirecAPIError(
                    f"Navirec API error {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
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

    def get_areas(self, account_id=None):
        """Return active Navirec areas/POIs available to the integration user."""
        areas = []
        next_url = self.api_url + "areas/"
        params = {
            "ordering": "id",
            "page_size": 1000,
            "active": True,
        }
        if account_id:
            params["account"] = account_id
        while next_url:
            response = self._request("GET", next_url, params=params)
            data = response.json()
            if isinstance(data, list):
                areas.extend(data)
            elif isinstance(data, dict):
                areas.extend(data.get("results", []))
            else:
                raise NavirecAPIError("Unexpected areas payload")
            next_url = response.links.get("next", {}).get("url")
            params = None
        return areas

    def get_vehicle_events(
        self,
        account_id=None,
        vehicle_ids=None,
        time_gte=None,
        time_lte=None,
        page_size=1000,
    ):
        """Return Navirec vehicle events for location/address enrichment.

        The endpoint accepts one scope filter. Live calls reject `vehicles`
        and `account` together ("could not be handled"), so a vehicle id uses
        `vehicle` and account is sent only when no vehicle id was given.
        """
        events = []
        next_url = self.api_url + "vehicle_events/"
        params = {
            "ordering": "time",
            "page_size": page_size,
        }
        if isinstance(vehicle_ids, str):
            vehicle_ids = [vehicle_ids]
        vehicle_ids = [vehicle_id for vehicle_id in (vehicle_ids or []) if vehicle_id]
        if vehicle_ids:
            params["vehicle"] = ",".join(vehicle_ids)
        elif account_id:
            params["account"] = account_id
        else:
            raise NavirecAPIError(
                "Vehicle events require an account or vehicle filter"
            )
        if time_gte:
            params["time__gte"] = time_gte
        if time_lte:
            params["time__lte"] = time_lte
        while next_url:
            response = self._request("GET", next_url, params=params)
            data = response.json()
            if isinstance(data, list):
                events.extend(data)
            elif isinstance(data, dict):
                events.extend(data.get("results", []))
            else:
                raise NavirecAPIError("Unexpected vehicle events payload")
            next_url = response.links.get("next", {}).get("url")
            params = None
        return events

    def get_trips(
        self,
        account_id=None,
        vehicle_ids=None,
        start_time_gte=None,
        end_time_lte=None,
        page_size=1000,
    ):
        """Return bounded Navirec trips for readable-location enrichment."""
        trips = []
        next_url = self.api_url + "trips/"
        params = {
            "ordering": "end_time",
            "page_size": page_size,
        }
        if account_id:
            params["account"] = account_id
        if vehicle_ids:
            if isinstance(vehicle_ids, str):
                params["vehicle"] = vehicle_ids
            else:
                params["vehicle__in"] = ",".join(filter(None, vehicle_ids))
        if start_time_gte:
            params["start_time__gte"] = start_time_gte
        if end_time_lte:
            params["end_time__lte"] = end_time_lte
        if not account_id and not vehicle_ids:
            raise NavirecAPIError("Trips require an account or vehicle filter")
        while next_url:
            response = self._request("GET", next_url, params=params)
            data = response.json()
            if isinstance(data, list):
                trips.extend(data)
            elif isinstance(data, dict):
                trips.extend(data.get("results", []))
            else:
                raise NavirecAPIError("Unexpected trips payload")
            next_url = response.links.get("next", {}).get("url")
            params = None
        return trips

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

    def _get_configuration(self, account_id=None, user_id=None, version=None):
        params = {"app": "web"}
        if version:
            params["version"] = version
        if account_id:
            params["account"] = account_id
        if user_id:
            params["user"] = user_id
        response = self._request("GET", "configuration/", params=params)
        data = response.json()
        if not isinstance(data, dict):
            raise NavirecAPIError("Unexpected configuration payload")
        return data

    def _one_user_id(self, params):
        response = self._request("GET", "users/", params=params)
        data = response.json()
        if isinstance(data, list):
            users = data
        elif isinstance(data, dict):
            users = data.get("results") or []
        else:
            return False
        if len(users) != 1 or not isinstance(users[0], dict):
            return False
        return users[0].get("id") or False

    def _configuration_user_id(self, account_id=None):
        """Prefer the integration user. Fall back to the only visible user."""
        params = {"page_size": 2}
        if account_id:
            params["account"] = account_id
        try:
            user_id = self._one_user_id({**params, "is_integration": True})
        except NavirecAPIError as exc:
            if exc.status_code != 400:
                raise
            user_id = False
        if user_id:
            return user_id
        return self._one_user_id(params)

    def get_geocoding_context(self, account_id=None):
        """Return (https geocoding base URL, query params including key).

        The key comes from the account configuration Navirec's web app uses.
        It is never logged or stored by this method.
        """
        try:
            configuration = self._get_configuration(
                account_id=account_id,
                version=DEFAULT_WEB_APP_VERSION,
            )
        except NavirecAPIError as exc:
            if exc.status_code != 400:
                raise
            user_id = self._configuration_user_id(account_id)
            if user_id:
                try:
                    configuration = self._get_configuration(
                        account_id=account_id,
                        user_id=user_id,
                        version=DEFAULT_WEB_APP_VERSION,
                    )
                except NavirecAPIError as retry_exc:
                    if retry_exc.status_code != 400:
                        raise
                    # Passing account without a matching user is what Navirec
                    # rejects. The token can still identify the account.
                    configuration = self._get_configuration(
                        user_id=user_id,
                        version=DEFAULT_WEB_APP_VERSION,
                    )
            elif account_id:
                configuration = self._get_configuration(
                    version=DEFAULT_WEB_APP_VERSION,
                )
            else:
                raise
        services = configuration.get("services")
        geocoding = services.get("geocoding") if isinstance(services, dict) else None
        if not isinstance(geocoding, dict):
            raise NavirecAPIError("Navirec configuration has no geocoding service")
        params = {}
        for key, value in geocoding.items():
            if value is None or isinstance(value, dict):
                continue
            if isinstance(value, (list, tuple)):
                if not value or not all(
                    isinstance(item, (str, int, float)) and not isinstance(item, bool)
                    for item in value
                ):
                    continue
                params[key] = ",".join(str(item) for item in value)
                continue
            if isinstance(value, bool):
                params[key] = "true" if value else "false"
                continue
            text = str(value).strip()
            if text:
                params[key] = text
        if not params.get("key"):
            raise NavirecAPIError(
                "Navirec geocoding configuration has no key (fields: %s)"
                % ", ".join(sorted(geocoding))
            )
        environment = configuration.get("environment")
        base_url = ""
        if isinstance(environment, dict):
            base_url = environment.get("geocoding_api_url") or ""
        base_url = str(base_url or DEFAULT_GEOCODING_URL).strip()
        if not base_url.startswith("https://"):
            raise NavirecAPIError("Navirec geocoding URL is not HTTPS")
        return base_url, params

    def reverse_geocode(self, longitude, latitude, base_url, query_params):
        """Return formatted_address from Navirec's reverse geocoder, or False.

        Official contract: GET <geocoding_api_url>/reverse/ with key, longitude,
        latitude, and HTTP Bearer. A Token scheme retry covers integration
        tokens that the geocoder accepts on the API scheme instead.
        """
        params = dict(query_params or {})
        if not params.get("key"):
            raise NavirecAPIError("Navirec geocoding configuration has no key")
        params["longitude"] = longitude
        params["latitude"] = latitude
        url = str(base_url).rstrip("/") + "/reverse/"
        last_status = None
        for scheme in ("Bearer", "Token"):
            try:
                response = requests.request(
                    "GET",
                    url,
                    headers={
                        "Authorization": f"{scheme} {self.token}",
                        "Accept": "application/json",
                        "User-Agent": self.user_agent,
                    },
                    params=params,
                    timeout=30,
                )
            except requests.RequestException:
                # The requests error includes the URL, and the URL contains the key.
                raise NavirecAPIError("Navirec geocoding network error") from None
            last_status = response.status_code
            if response.status_code == 401 and scheme == "Bearer":
                continue
            if response.status_code in (401, 403):
                raise NavirecAPIError(
                    "Navirec geocoding rejected the integration token",
                    status_code=response.status_code,
                )
            if response.status_code >= 400:
                raise NavirecAPIError(
                    f"Navirec geocoding error {response.status_code}",
                    status_code=response.status_code,
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise NavirecAPIError(
                    "Navirec geocoding returned invalid JSON"
                ) from exc
            features = data.get("features") if isinstance(data, dict) else None
            if not features or not isinstance(features[0], dict):
                return False
            props = features[0].get("properties") or {}
            address = props.get("formatted_address")
            if not isinstance(address, str):
                return False
            return address.strip() or False
        raise NavirecAPIError(
            "Navirec geocoding rejected the integration token",
            status_code=last_status,
        )


def public_navirec_error(message):
    """Return an error safe to show in the UI. Query keys stay out of it."""
    text = re.sub(
        r"(?i)(key(?:\"?\s*[:=]\s*\"?))[^\s&,\"'}]+",
        r"\1redacted",
        str(message or ""),
    )
    return text[:300]


def vehicle_uuid_from_url(url):
    if not url:
        return False
    path = urlparse(url).path.rstrip("/")
    value = path.split("/")[-1] if path else ""
    return value or False
