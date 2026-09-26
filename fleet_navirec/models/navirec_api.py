import base64
import binascii
import json
import logging
import re
import time
from urllib.parse import urljoin, urlparse
from uuid import UUID

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
                 user_agent=DEFAULT_USER_AGENT, timezone=None, user_id=None):
        self.api_url = (api_url or DEFAULT_API_URL).rstrip("/") + "/"
        self.token = token or ""
        self.version = version or DEFAULT_API_VERSION
        self.user_agent = user_agent
        self.timezone = timezone
        self.user_id = user_id

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
        """Use exactly one scope: vehicle for one ID, vehicles for a batch.

        Do not combine vehicle scopes with account. Normalize and deduplicate
        IDs before deciding which documented filter to use. Never widen an
        explicitly empty/invalid vehicle selection to the whole account.
        """
        params = {"ordering": "time", "page_size": page_size}
        if vehicle_ids is not None:
            if isinstance(vehicle_ids, str):
                vehicle_ids = vehicle_ids.split(",")
            if not isinstance(vehicle_ids, (list, tuple, set)) or any(
                not isinstance(value, str) for value in vehicle_ids
            ):
                raise NavirecAPIError("Vehicle IDs must be strings")
            ids = list(dict.fromkeys(value.strip() for value in vehicle_ids if value.strip()))
            if not ids or any("," in value for value in ids):
                raise NavirecAPIError("Provide a non-empty list of individual vehicle IDs")
            params["vehicle" if len(ids) == 1 else "vehicles"] = ",".join(ids)
        elif account_id:
            params["account"] = account_id
        else:
            raise NavirecAPIError("Vehicle events require an account or vehicle filter")
        if time_gte:
            params["time__gte"] = time_gte
        if time_lte:
            params["time__lte"] = time_lte
        events = []
        next_url = self.api_url + "vehicle_events/"
        visited = set()
        # Enrichment must not paginate without a bound inside a GPS cron.
        for _page in range(20):
            if next_url in visited:
                raise NavirecAPIError("Navirec repeated a vehicle-events pagination URL")
            visited.add(next_url)
            response = self._request("GET", next_url, params=params)
            data = response.json()
            rows = data if isinstance(data, list) else (
                data.get("results") if isinstance(data, dict) else None
            )
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise NavirecAPIError("Unexpected vehicle events payload")
            events.extend(rows)
            next_link = response.links.get("next", {}).get("url")
            if not next_link:
                return events
            next_url = urljoin(next_url, next_link)
            parsed, origin = urlparse(next_url), urlparse(self.api_url)
            if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                raise NavirecAPIError("Unsafe Navirec vehicle-events pagination URL")
            params = None
        raise NavirecAPIError("Navirec vehicle-events pagination limit reached")

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

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str):
            return False
        try:
            return str(UUID(value.strip()))
        except (ValueError, AttributeError):
            return False

    def _token_user_id(self):
        """Read ONLY a UUID user_id routing hint from our own token.

        This is NOT local authentication or JWT signature validation. Navirec
        receives the unchanged token and authenticates every request. Never use
        this hint to grant access, select another user, or change account scope.
        Opaque tokens need an explicitly configured token-owner UUID instead.
        """
        if not isinstance(self.token, str) or len(self.token) > 32768:
            return False
        parts = self.token.split(".")
        if len(parts) != 3 or not all(parts):
            return False
        try:
            payload = parts[1].encode("ascii")
            payload += b"=" * (-len(payload) % 4)
            claims = json.loads(base64.b64decode(payload, altchars=b"-_", validate=True))
        except (ValueError, UnicodeError, binascii.Error):
            return False
        return self._uuid(claims.get("user_id")) if isinstance(claims, dict) else False

    def _configuration_user_id(self, account_id=None):
        """Resolve the token owner; never guess from a list of account users."""
        token_user = self._token_user_id()
        explicit = self._uuid(self.user_id) if self.user_id else False
        if self.user_id and not explicit:
            raise NavirecAPIError("Navirec Integration User UUID is not a valid UUID")
        if explicit and token_user and explicit != token_user:
            raise NavirecAPIError(
                "Navirec Integration User UUID does not match the token user_id"
            )
        if explicit or token_user:
            return explicit or token_user
        raise NavirecAPIError(
            "Cannot determine this token's owner. Set Integration User UUID in "
            "Fleet > Settings > Navirec to the user that owns this API token."
        )

    def get_geocoding_context(self, account_id=None):
        """Request configuration with the account AND the token-owner user.

        A 403 is a permission denial, not a reason to drop account scope or try
        another user. Geocoding keys remain only in memory, never in logs/git.
        """
        if not account_id:
            raise NavirecAPIError("Set the Navirec Account ID before checking geocoding")
        user_id = self._configuration_user_id(account_id)
        try:
            configuration = self._get_configuration(
                account_id=account_id,
                user_id=user_id,
                version=DEFAULT_WEB_APP_VERSION,
            )
        except NavirecAPIError as exc:
            if exc.status_code == 403:
                raise NavirecAPIError(
                    "Navirec denied /configuration/ (403) with account and token owner. "
                    "Confirm this integration user's configuration/geocoding access "
                    "with Navirec. GPS access alone does not prove geocoding access.",
                    status_code=403,
                ) from None
            raise
        for name, expected in (("account", account_id), ("user", user_id)):
            scope = configuration.get(name)
            returned_id = scope.get("id") if isinstance(scope, dict) else (
                vehicle_uuid_from_url(scope) if isinstance(scope, str) else None
            )
            if returned_id and str(returned_id) != str(expected):
                raise NavirecAPIError("Navirec configuration scope does not match the request")
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
        self._check_geocoding_url(base_url)
        return base_url, params

    @staticmethod
    def _check_geocoding_url(base_url):
        try:
            parsed = urlparse(str(base_url))
        except ValueError:
            raise NavirecAPIError("Malformed Navirec geocoding URL") from None
        if (
            parsed.scheme != "https"
            or parsed.netloc not in ("realtime.navirec.com", "realtime.navirec.com:443")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise NavirecAPIError("Navirec geocoding URL is not an approved HTTPS endpoint")

    def reverse_geocode(self, longitude, latitude, base_url, query_params):
        """Return formatted_address from Navirec's reverse geocoder, or False.

        Observed Navirec web-client format: GET /reverse/ with key, longitude,
        latitude, and HTTP Bearer. A Token scheme retry covers integration
        tokens that the geocoder accepts on the API scheme instead.
        """
        self._check_geocoding_url(base_url)
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
                    allow_redirects=False,
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
            if response.status_code >= 300:
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
            if not isinstance(features, list) or not features or not isinstance(features[0], dict):
                return False
            props = features[0].get("properties") or {}
            address = props.get("formatted_address") if isinstance(props, dict) else None
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
