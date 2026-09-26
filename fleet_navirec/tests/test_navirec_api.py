import base64
import json

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
                "vehicles": "one,two",
                "time__gte": "2026-09-25T00:00:00Z",
                "time__lte": "2026-09-25T23:59:59Z",
            },
        )
        self.assertIsNone(request.call_args_list[1].kwargs["params"])

    def test_get_vehicle_events_single_vehicle_omits_account(self):
        response = self._response([])
        with patch.object(
            navirec_api.requests, "request", return_value=response
        ) as request:
            NavirecClient(token="x").get_vehicle_events(
                account_id="acct",
                vehicle_ids=["54c0b29b-2535-4a47-9165-f7d83fb582b8"],
                time_gte="2026-09-25T00:00:00Z",
            )

        params = request.call_args.kwargs["params"]
        self.assertEqual(
            params["vehicle"],
            "54c0b29b-2535-4a47-9165-f7d83fb582b8",
        )
        self.assertNotIn("account", params)
        self.assertNotIn("vehicles", params)

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

    USER_ID = "11111111-1111-4111-8111-111111111111"
    OTHER_USER_ID = "22222222-2222-4222-8222-222222222222"

    def _jwt(self, claims):
        # Synthetic token: never sent to a real service. Only HTTP mocks use it.
        def encode(data):
            return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
        return "%s.%s.test-signature" % (encode({"alg": "HS256"}), encode(claims))

    def _geocoding_configuration(self):
        return self._response({
            "account": {"id": "acct"},
            "user": {"id": self.USER_ID},
            "services": {"geocoding": {"key": "account-key", "backends": ["navirec"]}},
            "environment": {
                "geocoding_api_url": "https://realtime.navirec.com/geocoding/"
            },
        })

    def test_geocoding_context_sends_account_and_explicit_owner_first(self):
        client = NavirecClient(token="secret", user_id=self.USER_ID)
        with patch.object(navirec_api.requests, "request", return_value=self._geocoding_configuration()) as request:
            base_url, params = client.get_geocoding_context(account_id="acct")
        self.assertEqual(base_url, "https://realtime.navirec.com/geocoding/")
        self.assertEqual(params, {"key": "account-key", "backends": "navirec"})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["params"], {
            "app": "web", "account": "acct", "user": self.USER_ID,
            "version": navirec_api.DEFAULT_WEB_APP_VERSION,
        })
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Token secret")
        self.assertNotIn("account-key", repr(vars(client)))

    def test_geocoding_context_uses_own_token_user_id_without_listing_users(self):
        token = self._jwt({"user_id": self.USER_ID})
        with patch.object(navirec_api.requests, "request", return_value=self._geocoding_configuration()) as request:
            NavirecClient(token=token).get_geocoding_context(account_id="acct")
        self.assertEqual(request.call_count, 1)
        self.assertTrue(request.call_args.args[1].endswith("/configuration/"))
        self.assertEqual(request.call_args.kwargs["params"]["user"], self.USER_ID)
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Token " + token)

    def test_geocoding_opaque_token_requires_explicit_owner_without_http(self):
        with patch.object(navirec_api.requests, "request") as request:
            with self.assertRaisesRegex(NavirecAPIError, "Set Integration User UUID"):
                NavirecClient(token="opaque-secret").get_geocoding_context(account_id="acct")
        request.assert_not_called()

    def test_geocoding_rejects_owner_mismatch_without_http(self):
        token = self._jwt({"user_id": self.USER_ID})
        with patch.object(navirec_api.requests, "request") as request:
            with self.assertRaisesRegex(NavirecAPIError, "does not match"):
                NavirecClient(token=token, user_id=self.OTHER_USER_ID).get_geocoding_context(account_id="acct")
        request.assert_not_called()

    def test_geocoding_requires_account_and_never_drops_scope(self):
        with patch.object(navirec_api.requests, "request") as request:
            with self.assertRaisesRegex(NavirecAPIError, "Account ID"):
                NavirecClient(token="secret", user_id=self.USER_ID).get_geocoding_context()
        request.assert_not_called()

    def test_configuration_403_does_not_retry_or_try_other_users(self):
        denied = self._response({"detail": "denied"}, status=403)
        client = NavirecClient(token="secret", user_id=self.USER_ID)
        with patch.object(navirec_api.requests, "request", return_value=denied) as request:
            with self.assertRaises(NavirecAPIError) as caught:
                client.get_geocoding_context(account_id="acct")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("configuration/geocoding access", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["params"]["user"], self.USER_ID)
        self.assertEqual(request.call_args.kwargs["params"]["account"], "acct")
        self.assertTrue(request.call_args.args[1].endswith("/configuration/"))

    def test_configuration_400_is_not_retried_with_broader_scope(self):
        denied = self._response({}, status=400, text="Bad request")
        with patch.object(navirec_api.requests, "request", return_value=denied) as request:
            with self.assertRaises(NavirecAPIError) as caught:
                NavirecClient(token="secret", user_id=self.USER_ID).get_geocoding_context(account_id="acct")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(request.call_count, 1)

    def test_configuration_rejects_wrong_account_or_user_response(self):
        for field in ("account", "user"):
            with self.subTest(field=field):
                response = self._geocoding_configuration()
                payload = response.json.return_value
                payload[field] = {"id": self.OTHER_USER_ID}
                with patch.object(navirec_api.requests, "request", return_value=response):
                    with self.assertRaisesRegex(NavirecAPIError, "scope does not match"):
                        NavirecClient(token="secret", user_id=self.USER_ID).get_geocoding_context(account_id="acct")

    def test_invalid_token_metadata_is_never_used_as_user_identity(self):
        tokens = ["opaque", "a.@@.z", "a..z", "a.e30.z", "x" * 40000,
                  self._jwt([]), self._jwt({"user_id": 42}),
                  self._jwt({"user_id": "not-a-uuid"}),
                  self._jwt({"sub": self.USER_ID})]
        for token in tokens:
            with self.subTest(token_length=len(token)):
                self.assertFalse(NavirecClient(token=token)._token_user_id())

    def test_invalid_explicit_user_uuid_is_rejected_without_http(self):
        with patch.object(navirec_api.requests, "request") as request:
            with self.assertRaisesRegex(NavirecAPIError, "valid UUID"):
                NavirecClient(token="secret", user_id="not-a-uuid").get_geocoding_context(account_id="acct")
        request.assert_not_called()

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
                NavirecClient(token="secret", user_id=self.USER_ID).get_geocoding_context(account_id="acct")

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

    def test_events_batch_contract_never_sends_a_list_in_vehicle(self):
        ids = [self.USER_ID, self.OTHER_USER_ID]
        def server_contract(method, url, **kwargs):
            params = kwargs["params"]
            self.assertNotIn("account", params)
            if "vehicle" in params and "," in params["vehicle"]:
                return self._response({}, status=400, text='{"vehicle":["This query argument could not be handled"]}')
            self.assertEqual(params.get("vehicles"), ",".join(ids))
            return self._response([])
        with patch.object(navirec_api.requests, "request", side_effect=server_contract) as request:
            self.assertEqual(NavirecClient(token="x").get_vehicle_events(account_id="acct", vehicle_ids=ids), [])
        self.assertEqual(request.call_count, 1)

    def test_events_deduplicate_and_choose_single_scope(self):
        with patch.object(navirec_api.requests, "request", return_value=self._response([])) as request:
            NavirecClient(token="x").get_vehicle_events(account_id="acct", vehicle_ids=[" " + self.USER_ID, self.USER_ID])
        self.assertEqual(request.call_args.kwargs["params"]["vehicle"], self.USER_ID)
        self.assertNotIn("vehicles", request.call_args.kwargs["params"])
        self.assertNotIn("account", request.call_args.kwargs["params"])

    def test_events_account_scope_without_vehicle_list(self):
        with patch.object(navirec_api.requests, "request", return_value=self._response([])) as request:
            NavirecClient(token="x").get_vehicle_events(account_id="acct")
        self.assertEqual(request.call_args.kwargs["params"]["account"], "acct")
        self.assertNotIn("vehicle", request.call_args.kwargs["params"])
        self.assertNotIn("vehicles", request.call_args.kwargs["params"])

    def test_events_empty_or_invalid_explicit_list_never_widens_to_account(self):
        for value in ([], [" "], [None], 42, {"id": "one"}, ["one,two"]):
            with self.subTest(value=value):
                with patch.object(navirec_api.requests, "request") as request:
                    with self.assertRaises(NavirecAPIError):
                        NavirecClient(token="x").get_vehicle_events(account_id="acct", vehicle_ids=value)
                request.assert_not_called()

    def test_events_rejects_malformed_results(self):
        for payload in ({"results": None}, {"results": {}}, [None], "invalid"):
            with self.subTest(payload=payload):
                with patch.object(navirec_api.requests, "request", return_value=self._response(payload)):
                    with self.assertRaisesRegex(NavirecAPIError, "Unexpected vehicle events"):
                        NavirecClient(token="x").get_vehicle_events(vehicle_ids=[self.USER_ID])

    def test_events_pagination_stays_on_navirec_origin(self):
        response = self._response([], links={"next": {"url": "https://other.invalid/events/"}})
        with patch.object(navirec_api.requests, "request", return_value=response) as request:
            with self.assertRaisesRegex(NavirecAPIError, "Unsafe"):
                NavirecClient(token="x").get_vehicle_events(vehicle_ids=[self.USER_ID])
        self.assertEqual(request.call_count, 1)

    def test_events_repeated_pagination_url_stops(self):
        response = self._response([], links={"next": {"url": "https://api.navirec.com/vehicle_events/"}})
        with patch.object(navirec_api.requests, "request", return_value=response) as request:
            with self.assertRaisesRegex(NavirecAPIError, "repeated"):
                NavirecClient(token="x").get_vehicle_events(vehicle_ids=[self.USER_ID])
        self.assertEqual(request.call_count, 1)

    def test_events_pagination_has_a_hard_limit(self):
        counter = iter(range(30))
        def page(*args, **kwargs):
            return self._response([], links={"next": {"url": "https://api.navirec.com/vehicle_events/?page=%s" % next(counter)}})
        with patch.object(navirec_api.requests, "request", side_effect=page) as request:
            with self.assertRaisesRegex(NavirecAPIError, "limit reached"):
                NavirecClient(token="x").get_vehicle_events(vehicle_ids=[self.USER_ID])
        self.assertEqual(request.call_count, 20)

    def test_reverse_geocode_rejects_untrusted_hosts_without_sending_credentials(self):
        for url in ("https://[invalid", "http://realtime.navirec.com/geocoding/", "https://other.invalid/",
                    "https://realtime.navirec.com.other.invalid/", "https://user@realtime.navirec.com/",
                    "https://realtime.navirec.com/geocoding/?key=secret"):
            with self.subTest(url=url):
                with patch.object(navirec_api.requests, "request") as request:
                    with self.assertRaises(NavirecAPIError):
                        NavirecClient(token="secret").reverse_geocode(1, 2, url, {"key": "account-key"})
                request.assert_not_called()

    def test_reverse_geocode_does_not_follow_redirects_or_retry_403(self):
        for status in (302, 403):
            with self.subTest(status=status):
                with patch.object(navirec_api.requests, "request", return_value=self._response({}, status=status)) as request:
                    with self.assertRaises(NavirecAPIError):
                        NavirecClient(token="secret").reverse_geocode(1, 2, navirec_api.DEFAULT_GEOCODING_URL, {"key": "account-key"})
                self.assertEqual(request.call_count, 1)
                self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_reverse_geocode_malformed_feature_is_a_safe_no_result(self):
        for data in ({"features": {}}, {"features": [None]}, {"features": [{"properties": []}]},
                     {"features": [{"properties": "invalid"}]}, []):
            with self.subTest(data=data):
                with patch.object(navirec_api.requests, "request", return_value=self._response(data)):
                    self.assertFalse(NavirecClient(token="secret").reverse_geocode(1, 2, navirec_api.DEFAULT_GEOCODING_URL, {"key": "account-key"}))

    def test_configuration_owner_is_sent_before_server_permission_check(self):
        def server_contract(method, url, **kwargs):
            query = kwargs["params"]
            if not query.get("user"):
                return self._response({}, status=403)
            self.assertEqual(query["user"], self.USER_ID)
            self.assertEqual(query["account"], "acct")
            return self._geocoding_configuration()
        token = self._jwt({"user_id": self.USER_ID})
        with patch.object(navirec_api.requests, "request", side_effect=server_contract) as request:
            _base_url, params = NavirecClient(token=token).get_geocoding_context(account_id="acct")
        self.assertEqual(params["key"], "account-key")
        self.assertEqual(request.call_count, 1)

    def test_events_relative_pagination_keeps_the_events_endpoint(self):
        pages = [self._response([], links={"next": {"url": "?cursor=two"}}), self._response([])]
        with patch.object(navirec_api.requests, "request", side_effect=pages) as request:
            self.assertEqual(NavirecClient(token="x").get_vehicle_events(vehicle_ids=[self.USER_ID]), [])
        self.assertEqual(request.call_args_list[1].args[1], "https://api.navirec.com/vehicle_events/?cursor=two")
        self.assertIsNone(request.call_args_list[1].kwargs["params"])
