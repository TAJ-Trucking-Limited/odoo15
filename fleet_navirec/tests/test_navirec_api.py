from unittest.mock import Mock, patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models import navirec_api
from ..models.navirec_api import (
    NavirecAPIError,
    NavirecClient,
    vehicle_uuid_from_url,
)


@tagged("post_install", "-at_install")
class TestNavirecAPI(TransactionCase):

    def _response(
        self,
        payload=None,
        status=200,
        links=None,
        headers=None,
        text="",
        invalid_json=False,
    ):
        response = Mock()
        response.status_code = status
        response.links = links or {}
        response.headers = headers or {}
        response.text = text
        if invalid_json:
            response.json.side_effect = ValueError("bad json")
        else:
            response.json.return_value = payload
        return response

    def test_headers_include_auth_version_agent_and_timezone(self):
        client = NavirecClient(
            token="secret",
            version="1.51.0",
            timezone="Africa/Dar_es_Salaam",
        )
        self.assertEqual(client.headers["Authorization"], "Token secret")
        self.assertIn("version=1.51.0", client.headers["Accept"])
        self.assertEqual(
            client.headers["User-Agent"],
            "TajOdoo/1.0.0 (fleet_navirec)",
        )
        self.assertEqual(
            client.headers["Accept-Timezone"],
            "Africa/Dar_es_Salaam",
        )

    def test_get_vehicles_follows_link_pagination(self):
        page_1 = self._response(
            [{"id": "one", "registration": "T000AAA"}],
            links={"next": {"url": "https://api.navirec.com/vehicles/?page=2"}},
        )
        page_2 = self._response(
            [{"id": "two", "registration": "T000BBB"}]
        )
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[page_1, page_2],
        ) as request:
            vehicles = NavirecClient(token="x").get_vehicles()

        self.assertEqual([item["id"] for item in vehicles], ["one", "two"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[0].kwargs["params"],
            {"ordering": "id", "page_size": 1000},
        )
        self.assertIsNone(request.call_args_list[1].kwargs["params"])

    def test_invalid_json_is_wrapped(self):
        response = self._response(invalid_json=True)
        with patch.object(
            navirec_api.requests, "request", return_value=response
        ):
            with self.assertRaisesRegex(
                NavirecAPIError, "invalid JSON"
            ):
                NavirecClient(token="x").test_connection()

    def test_401_is_wrapped(self):
        response = self._response(status=401, payload={})
        with patch.object(
            navirec_api.requests, "request", return_value=response
        ):
            with self.assertRaisesRegex(
                NavirecAPIError, "Invalid token"
            ):
                NavirecClient(token="bad").test_connection()

    def test_429_retries_once(self):
        limited = self._response(
            status=429,
            payload={},
            headers={"Retry-After": "0"},
        )
        success = self._response(payload=[])
        with (
            patch.object(
                navirec_api.requests,
                "request",
                side_effect=[limited, success],
            ) as request,
            patch.object(navirec_api.time, "sleep") as sleep,
        ):
            self.assertTrue(NavirecClient(token="x").test_connection())

        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_repeated_429_raises(self):
        limited_1 = self._response(
            status=429, payload={}, headers={"Retry-After": "0"}
        )
        limited_2 = self._response(status=429, payload={})
        with (
            patch.object(
                navirec_api.requests,
                "request",
                side_effect=[limited_1, limited_2],
            ),
            patch.object(navirec_api.time, "sleep"),
        ):
            with self.assertRaisesRegex(
                NavirecAPIError, "rate limit exceeded"
            ):
                NavirecClient(token="x").test_connection()

    def test_get_areas_follows_pagination_and_filters_active_account(self):
        page_1 = self._response(
            [{"id": "area-one", "name": "Depot"}],
            links={"next": {"url": "https://api.navirec.com/areas/?page=2"}},
        )
        page_2 = self._response([
            {"id": "area-two", "name": "Border"},
        ])
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[page_1, page_2],
        ) as request:
            areas = NavirecClient(token="x").get_areas(account_id="acct")

        self.assertEqual([item["id"] for item in areas], ["area-one", "area-two"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[0].kwargs["params"],
            {
                "ordering": "id",
                "page_size": 1000,
                "active": True,
                "account": "acct",
            },
        )
        self.assertIsNone(request.call_args_list[1].kwargs["params"])

    def test_get_vehicle_events_uses_bounded_filters_and_pagination(self):
        page_1 = self._response(
            [{"id": "event-one", "address": "Itezi, Mbeya"}],
            links={
                "next": {
                    "url": "https://api.navirec.com/vehicle_events/?cursor=next"
                }
            },
        )
        page_2 = self._response([
            {"id": "event-two", "address": "Mbeya, Tanzania"},
        ])
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[page_1, page_2],
        ) as request:
            events = NavirecClient(token="x").get_vehicle_events(
                account_id="acct",
                vehicle_ids=["one", "two"],
                time_gte="2026-09-25T00:00:00Z",
                time_lte="2026-09-25T23:59:59Z",
            )

        self.assertEqual([item["id"] for item in events], ["event-one", "event-two"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[0].kwargs["params"],
            {
                "ordering": "time",
                "page_size": 1000,
                "account": "acct",
                "vehicles": "one,two",
                "time__gte": "2026-09-25T00:00:00Z",
                "time__lte": "2026-09-25T23:59:59Z",
            },
        )
        self.assertIsNone(request.call_args_list[1].kwargs["params"])

    def test_get_vehicle_events_requires_filter(self):
        with self.assertRaisesRegex(NavirecAPIError, "require an account or vehicle"):
            NavirecClient(token="x").get_vehicle_events(
                time_gte="2026-09-25T00:00:00Z"
            )

    def test_get_trips_uses_bounded_vehicle_filters_and_pagination(self):
        page_1 = self._response(
            [{"id": "trip-one", "end_address": "Itezi, Mbeya"}],
            links={
                "next": {"url": "https://api.navirec.com/trips/?cursor=next"}
            },
        )
        page_2 = self._response([
            {"id": "trip-two", "end_address": "Mbeya, Tanzania"},
        ])
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[page_1, page_2],
        ) as request:
            trips = NavirecClient(token="x").get_trips(
                account_id="acct",
                vehicle_ids=["one", "two"],
                start_time_gte="2026-08-25T00:00:00Z",
                end_time_lte="2026-09-25T23:59:59Z",
            )

        self.assertEqual([item["id"] for item in trips], ["trip-one", "trip-two"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[0].kwargs["params"],
            {
                "ordering": "end_time",
                "page_size": 1000,
                "account": "acct",
                "vehicle__in": "one,two",
                "start_time__gte": "2026-08-25T00:00:00Z",
                "end_time__lte": "2026-09-25T23:59:59Z",
            },
        )
        self.assertIsNone(request.call_args_list[1].kwargs["params"])

    def test_get_trips_requires_filter(self):
        with self.assertRaisesRegex(NavirecAPIError, "require an account or vehicle"):
            NavirecClient(token="x").get_trips(
                start_time_gte="2026-08-25T00:00:00Z"
            )

    def test_last_vehicle_states_follows_pagination(self):
        page_1 = self._response(
            [{"vehicle": "https://api.navirec.com/vehicles/one/"}],
            links={
                "next": {
                    "url": "https://api.navirec.com/last_vehicle_states/?page=2"
                }
            },
        )
        page_2 = self._response(
            [{"vehicle": "https://api.navirec.com/vehicles/two/"}]
        )
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[page_1, page_2],
        ):
            states = NavirecClient(token="x").get_last_vehicle_states(
                account_id="acct"
            )
        self.assertEqual(len(states), 2)

    def test_vehicle_uuid_from_url(self):
        self.assertEqual(
            vehicle_uuid_from_url(
                "https://api.navirec.com/vehicles/"
                "11111111-1111-4111-8111-111111111111/"
            ),
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertFalse(vehicle_uuid_from_url(""))

    def test_geocoding_context_reads_configuration_without_storing_key(self):
        configuration = self._response({
            "services": {"geocoding": {"key": "account-key", "backends": ["navirec"]}},
            "environment": {
                "geocoding_api_url": "https://realtime.navirec.com/geocoding/"
            },
        })
        with patch.object(
            navirec_api.requests, "request", return_value=configuration
        ) as request:
            base_url, params = NavirecClient(token="secret").get_geocoding_context(
                account_id="acct"
            )

        self.assertEqual(base_url, "https://realtime.navirec.com/geocoding/")
        self.assertEqual(params, {"key": "account-key", "backends": "navirec"})
        sent = request.call_args.kwargs["params"]
        self.assertEqual(sent["app"], "web")
        self.assertEqual(sent["account"], "acct")
        self.assertEqual(sent["version"], navirec_api.DEFAULT_WEB_APP_VERSION)
        self.assertEqual(
            request.call_args.kwargs["headers"]["Authorization"],
            "Token secret",
        )

    def test_geocoding_context_retries_when_configuration_requires_user(self):
        missing_user = self._response(
            {"detail": "user is required"},
            status=400,
            text="user is required",
        )
        users = self._response([{"id": "user-1"}])
        configuration = self._response({
            "services": {"geocoding": {"key": "account-key"}},
            "environment": {
                "geocoding_api_url": "https://realtime.navirec.com/geocoding/"
            },
        })
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[missing_user, users, configuration],
        ) as request:
            base_url, params = NavirecClient(token="secret").get_geocoding_context(
                account_id="acct"
            )

        self.assertEqual(base_url, "https://realtime.navirec.com/geocoding/")
        self.assertEqual(params["key"], "account-key")
        self.assertTrue(
            request.call_args_list[1].kwargs["params"]["is_integration"]
        )
        self.assertEqual(request.call_args_list[2].kwargs["params"]["user"], "user-1")
        self.assertEqual(request.call_args_list[2].kwargs["params"]["account"], "acct")

    def test_geocoding_context_drops_account_when_that_filter_is_rejected(self):
        missing_user = self._response(
            {"detail": "user is required"},
            status=400,
            text="user is required",
        )
        rejected_account = self._response(
            {"detail": "account rejected"},
            status=400,
            text="account rejected",
        )
        users = self._response([{"id": "user-1"}])
        configuration = self._response({
            "services": {"geocoding": {"key": "account-key"}},
            "environment": {
                "geocoding_api_url": "https://realtime.navirec.com/geocoding/"
            },
        })
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[missing_user, users, rejected_account, configuration],
        ) as request:
            _base_url, params = NavirecClient(token="secret").get_geocoding_context(
                account_id="acct"
            )

        self.assertEqual(params["key"], "account-key")
        sent = request.call_args_list[3].kwargs["params"]
        self.assertEqual(sent["user"], "user-1")
        self.assertNotIn("account", sent)

    def test_public_navirec_error_redacts_geocoding_key(self):
        text = navirec_api.public_navirec_error(
            "request failed key=account-key&latitude=1"
        )
        self.assertNotIn("account-key", text)
        self.assertIn("redacted", text)

    def test_geocoding_context_rejects_missing_key_without_echoing_secrets(self):
        configuration = self._response({
            "services": {"geocoding": {"api_key": "secret-value"}},
            "environment": {
                "geocoding_api_url": "https://realtime.navirec.com/geocoding/"
            },
        })
        with patch.object(
            navirec_api.requests, "request", return_value=configuration
        ):
            with self.assertRaises(NavirecAPIError) as caught:
                NavirecClient(token="secret").get_geocoding_context()

        self.assertIn("api_key", str(caught.exception))
        self.assertNotIn("secret-value", str(caught.exception))

    def test_reverse_geocode_reads_formatted_address_with_bearer(self):
        payload = self._response({
            "features": [{
                "properties": {"formatted_address": "Itezi, Mbeya, Tanzania"}
            }]
        })
        with patch.object(
            navirec_api.requests, "request", return_value=payload
        ) as request:
            address = NavirecClient(token="secret").reverse_geocode(
                33.5219450,
                -8.9064783,
                "https://realtime.navirec.com/geocoding/",
                {"key": "account-key", "backends": "navirec"},
            )

        self.assertEqual(address, "Itezi, Mbeya, Tanzania")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(
            request.call_args.kwargs["headers"]["Authorization"],
            "Bearer secret",
        )
        self.assertEqual(
            request.call_args.kwargs["params"],
            {
                "key": "account-key",
                "backends": "navirec",
                "longitude": 33.5219450,
                "latitude": -8.9064783,
            },
        )
        self.assertTrue(request.call_args.args[1].endswith("/reverse/"))

    def test_reverse_geocode_retries_with_token_scheme_after_bearer_401(self):
        denied = self._response({"detail": "Could not validate credentials"}, status=401)
        payload = self._response({
            "features": [{
                "properties": {"formatted_address": "Itezi, Mbeya, Tanzania"}
            }]
        })
        with patch.object(
            navirec_api.requests,
            "request",
            side_effect=[denied, payload],
        ) as request:
            address = NavirecClient(token="secret").reverse_geocode(
                33.5219450,
                -8.9064783,
                "https://realtime.navirec.com/geocoding/",
                {"key": "account-key"},
            )

        self.assertEqual(address, "Itezi, Mbeya, Tanzania")
        self.assertEqual(
            request.call_args_list[0].kwargs["headers"]["Authorization"],
            "Bearer secret",
        )
        self.assertEqual(
            request.call_args_list[1].kwargs["headers"]["Authorization"],
            "Token secret",
        )
