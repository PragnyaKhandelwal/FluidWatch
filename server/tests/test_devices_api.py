from datetime import UTC, datetime, timedelta

from app.models import Reading

from .conftest import AUTH, make_reading


def test_no_devices(client):
    assert client.get("/api/v1/devices").json() == []


def test_one_row_per_device_from_latest_reading(client):
    client.post("/api/v1/readings", json=make_reading(device_id="B", battery_percent=50), headers=AUTH)
    client.post("/api/v1/readings", json=make_reading(device_id="A", battery_percent=80), headers=AUTH)
    client.post("/api/v1/readings", json=make_reading(device_id="A", battery_percent=79, wifi_rssi=-70), headers=AUTH)

    devices = client.get("/api/v1/devices").json()
    assert [d["device_id"] for d in devices] == ["A", "B"]  # sorted by id
    a = devices[0]
    assert a["battery_percent"] == 79
    assert a["wifi_rssi"] == -70
    assert a["online"] is True


def test_device_goes_offline_after_window(client, db):
    stale = make_reading(device_id="OLD")
    db.add(Reading(**stale, received_at=datetime.now(UTC) - timedelta(minutes=5)))
    db.commit()
    client.post("/api/v1/readings", json=make_reading(device_id="NEW"), headers=AUTH)

    online = {d["device_id"]: d["online"] for d in client.get("/api/v1/devices").json()}
    assert online == {"NEW": True, "OLD": False}
