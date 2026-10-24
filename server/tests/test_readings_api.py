import csv
import io

from openpyxl import load_workbook

from .conftest import AUTH, make_reading


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ingest_requires_bearer_token(client):
    assert client.post("/api/v1/readings", json=make_reading()).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post("/api/v1/readings", json=make_reading(), headers=bad).status_code == 401


def test_ingest_rejects_invalid_payload(client):
    body = make_reading()
    del body["fluid_level_percent"]
    assert client.post("/api/v1/readings", json=body, headers=AUTH).status_code == 422


def test_ingest_stores_and_returns_reading(client):
    res = client.post("/api/v1/readings", json=make_reading(), headers=AUTH)
    assert res.status_code == 201
    out = res.json()
    assert out["id"] > 0
    assert out["device_id"] == "IV-STAND-01"
    assert out["fluid_level_percent"] == 80.0
    assert "received_at" in out

    listed = client.get("/api/v1/readings").json()
    assert [r["id"] for r in listed] == [out["id"]]


def test_list_filters_by_device_and_orders_newest_first(client):
    client.post("/api/v1/readings", json=make_reading(device_id="A", timestamp_ms=1), headers=AUTH)
    client.post("/api/v1/readings", json=make_reading(device_id="B", timestamp_ms=2), headers=AUTH)
    client.post("/api/v1/readings", json=make_reading(device_id="A", timestamp_ms=3), headers=AUTH)

    all_rows = client.get("/api/v1/readings").json()
    assert [r["timestamp_ms"] for r in all_rows] == [3, 2, 1]

    only_a = client.get("/api/v1/readings", params={"device_id": "A"}).json()
    assert {r["device_id"] for r in only_a} == {"A"}
    assert len(only_a) == 2

    limited = client.get("/api/v1/readings", params={"limit": 1}).json()
    assert len(limited) == 1


def test_list_limit_is_capped(client):
    assert client.get("/api/v1/readings", params={"limit": 5001}).status_code == 422


def test_latest(client):
    assert client.get("/api/v1/readings/latest").status_code == 404

    client.post("/api/v1/readings", json=make_reading(device_id="A", timestamp_ms=1), headers=AUTH)
    client.post("/api/v1/readings", json=make_reading(device_id="B", timestamp_ms=2), headers=AUTH)

    assert client.get("/api/v1/readings/latest").json()["device_id"] == "B"
    assert client.get("/api/v1/readings/latest", params={"device_id": "A"}).json()["timestamp_ms"] == 1
    assert client.get("/api/v1/readings/latest", params={"device_id": "nope"}).status_code == 404


def test_export_csv(client):
    client.post("/api/v1/readings", json=make_reading(timestamp_ms=1), headers=AUTH)
    client.post("/api/v1/readings", json=make_reading(timestamp_ms=2, alert="Fluid level low"), headers=AUTH)

    res = client.get("/api/v1/readings/export.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(res.text)))
    assert rows[0][:3] == ["id", "device_id", "timestamp_ms"]
    # Export is oldest-first, unlike the list endpoint.
    assert [r[2] for r in rows[1:]] == ["1", "2"]
    assert rows[2][-1] == "Fluid level low"


def test_export_xlsx(client):
    client.post("/api/v1/readings", json=make_reading(), headers=AUTH)

    res = client.get("/api/v1/readings/export.xlsx")
    assert res.status_code == 200

    sheet = load_workbook(io.BytesIO(res.content)).active
    values = list(sheet.values)
    assert sheet.title == "Readings"
    assert values[0][1] == "device_id"
    assert values[1][1] == "IV-STAND-01"
    # A null alert is written as an empty cell, not the string "None".
    assert values[1][-1] in ("", None)
