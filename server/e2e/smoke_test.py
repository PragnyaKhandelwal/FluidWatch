"""End-to-end smoke test against a *running* backend (e.g. the Docker
container in CI). Walks the same path a real deployment does:

    ESP32 uploads a frame -> real model scores it -> reading stored ->
    dashboard WebSocket receives the bed update -> REST/exports agree.

Usage:
    python e2e/smoke_test.py --base-url http://localhost:8000 --token <API_AUTH_TOKEN>

Needs only `httpx`, `websockets` and `pillow` - not the app itself, so it
tests the deployed artifact from the outside."""

import argparse
import asyncio
import io
import json
import sys
import time

import httpx
import websockets
from PIL import Image, ImageDraw

DEVICE_ID = f"E2E-CAM-{int(time.time())}"


def make_bag_photo() -> bytes:
    """A crude IV-bag-ish image - not for accuracy, just a real JPEG the
    model can chew on."""
    im = Image.new("RGB", (480, 640), (230, 230, 230))
    draw = ImageDraw.Draw(im)
    draw.rectangle((140, 80, 340, 560), outline=(60, 60, 60), width=6)
    draw.rectangle((146, 300, 334, 554), fill=(170, 210, 240))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def wait_for_health(client: httpx.Client, timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(2)
    raise SystemExit(f"backend never became healthy within {timeout_s}s")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"  ok  {message}")


async def run(base_url: str, token: str) -> None:
    auth = {"Authorization": f"Bearer {token}"}
    ws_url = base_url.replace("http", "ws", 1) + "/ws/bedfeed"

    with httpx.Client(base_url=base_url, timeout=60) as client:
        wait_for_health(client)
        check(True, "backend healthy")

        check(
            client.post("/api/v1/readings", json={}).status_code == 401,
            "unauthenticated ingest is rejected",
        )

        photo = make_bag_photo()
        async with websockets.connect(ws_url) as ws:
            res = client.post(
                "/api/v1/frames",
                params={"device_id": DEVICE_ID, "bed_label": "E2E Bed", "patient_name": "Smoke Test"},
                files={"file": ("frame.jpg", photo, "image/jpeg")},
                headers=auth,
            )
            check(res.status_code == 201, f"frame upload accepted ({res.status_code})")
            reading = res.json()
            check(0 <= reading["fluid_level_percent"] <= 100, f"model fill = {reading['fluid_level_percent']}%")
            check(reading["aux_class"] in {"empty", "50%", "80%", "full"}, f"aux class = {reading['aux_class']}")

            bed = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            check(bed["id"] == DEVICE_ID, "dashboard feed received the new bed")
            check(bed["bed"] == "E2E Bed" and bed["patient"] == "Smoke Test", "bed metadata carried through")
            check(bed["level"] == round(reading["fluid_level_percent"], 1), "feed level matches stored reading")
            check(bed["status"] in {"STABLE", "WATCH", "CRITICAL"}, f"feed status = {bed['status']}")

        frame = client.get("/api/v1/frames/latest", params={"device_id": DEVICE_ID})
        check(frame.status_code == 200 and frame.content == photo, "latest frame served back byte-for-byte")

        bad = client.post(
            "/api/v1/frames",
            params={"device_id": DEVICE_ID},
            files={"file": ("x.jpg", b"not an image", "image/jpeg")},
            headers=auth,
        )
        check(bad.status_code == 422, "corrupt upload rejected cleanly")

        latest = client.get("/api/v1/readings/latest", params={"device_id": DEVICE_ID}).json()
        check(latest["id"] == reading["id"], "latest reading is the uploaded one")

        devices = {d["device_id"]: d for d in client.get("/api/v1/devices").json()}
        check(devices.get(DEVICE_ID, {}).get("online") is True, "device listed as online")

        csv_text = client.get("/api/v1/readings/export.csv", params={"device_id": DEVICE_ID}).text
        check(DEVICE_ID in csv_text, "reading appears in CSV export")

        xlsx = client.get("/api/v1/readings/export.xlsx", params={"device_id": DEVICE_ID})
        check(xlsx.status_code == 200 and xlsx.content[:2] == b"PK", "XLSX export is a valid zip")

    print("E2E smoke test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.base_url.rstrip("/"), args.token))


if __name__ == "__main__":
    sys.exit(main())
