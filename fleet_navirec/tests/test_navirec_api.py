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
