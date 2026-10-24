"""The live path the dashboard depends on: an ingest (JSON or camera frame)
must reach every connected /ws/readings and /ws/bedfeed client."""

from .conftest import AUTH, make_jpeg, make_reading


def test_json_reading_is_broadcast_on_both_feeds(client):
    with client.websocket_connect("/ws/readings") as readings_ws, client.websocket_connect("/ws/bedfeed") as bed_ws:
        posted = client.post(
            "/api/v1/readings", json=make_reading(status="LOW_LEVEL", fluid_level_percent=12.34), headers=AUTH
        ).json()

        full = readings_ws.receive_json()
        assert full["id"] == posted["id"]
        assert full["status"] == "LOW_LEVEL"

        bed = bed_ws.receive_json()
        assert bed == {
            "id": "IV-STAND-01",
            "bed": "Bed 07",
            "patient": "Test Patient",
            "fluid": "Saline 0.9%",
            "flow": 120.0,
            "level": 12.3,
            "status": "WATCH",
            "auxClass": None,
            "battery": 90,
            "wifi": -55,
        }


def test_frame_upload_is_broadcast_with_aux_class(client, fake_processor):
    with client.websocket_connect("/ws/bedfeed") as bed_ws:
        client.post(
            "/api/v1/frames",
            params={"device_id": "CAM-07"},
            files={"file": ("f.jpg", make_jpeg(), "image/jpeg")},
            headers=AUTH,
        )
        bed = bed_ws.receive_json()
        assert bed["id"] == "CAM-07"
        assert bed["bed"] == "CAM-07"  # falls back to device_id with no bed_label
        assert bed["patient"] == "Unassigned"
        assert bed["level"] == 72.5
        assert bed["auxClass"] == "80%"


def test_every_connected_client_receives_the_update(client):
    with client.websocket_connect("/ws/bedfeed") as a, client.websocket_connect("/ws/bedfeed") as b:
        client.post("/api/v1/readings", json=make_reading(), headers=AUTH)
        assert a.receive_json()["id"] == b.receive_json()["id"] == "IV-STAND-01"


def test_disconnected_client_does_not_break_broadcast(client):
    with client.websocket_connect("/ws/bedfeed"):
        pass  # connect then close
    with client.websocket_connect("/ws/bedfeed") as live:
        assert client.post("/api/v1/readings", json=make_reading(), headers=AUTH).status_code == 201
        assert live.receive_json()["id"] == "IV-STAND-01"
