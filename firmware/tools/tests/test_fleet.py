"""fleet.py: the settings table it reads from the firmware source, validation
that mirrors the firmware's, the command payloads, and the Supabase calls
(against a mock transport)."""

import json
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fleet  # noqa: E402

PARAMS = fleet.load_params()
FRAME_SIZES = fleet.load_frame_sizes()


# --- the table in capture_config.cpp ------------------------------------------------


def test_table_matches_the_firmware_enum():
    header = (fleet.FIRMWARE_DIR / "include" / "capture_config.h").read_text(encoding="utf-8")
    enum_body = re.search(r"enum Param : uint8_t \{(.*?)P_COUNT", header, re.S).group(1)
    enum_keys = [name.lower() for name in re.findall(r"P_(\w+),", enum_body)]
    assert [p["key"] for p in PARAMS] == enum_keys


def test_every_default_is_in_range():
    for p in PARAMS:
        assert p["min"] <= p["default"] <= p["max"], p["key"]


def test_frame_sizes_match_esp32_camera():
    assert FRAME_SIZES[8] == "VGA" and FRAME_SIZES[9] == "SVGA" and FRAME_SIZES[-1] == "QSXGA"
    frame_size = next(p for p in PARAMS if p["key"] == "frame_size")
    assert frame_size["max"] == len(FRAME_SIZES) - 1


def test_firmware_defaults_include_labels():
    defaults = fleet.firmware_defaults(PARAMS)
    assert defaults["interval_s"] == 10 and defaults["camera_sleep"] == 1
    assert defaults["bed_label"] and defaults["fluid_label"]


# --- validation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("frame_size=SVGA", ("frame_size", 9)),
        ("frame_size=svga", ("frame_size", 9)),
        ("frame_size=10", ("frame_size", 10)),
        ("flash=on", ("flash", 1)),
        ("camera_sleep=false", ("camera_sleep", 0)),
        ("contrast=-2", ("contrast", -2)),
        ("cpu_mhz=80", ("cpu_mhz", 80)),
        ("bed_label=Bed 12", ("bed_label", "Bed 12")),
    ],
)
def test_parse_setting_accepts(text, expected):
    assert fleet.parse_setting(text, PARAMS, FRAME_SIZES) == expected


@pytest.mark.parametrize(
    "text, message",
    [
        ("contrast=3", "outside"),
        ("cpu_mhz=120", "80, 160 or 240"),
        ("frame_size=HUGE", "not a number"),
        ("nope=1", "unknown setting"),
        ("interval_s", "key=value"),
        ("bed_label=" + "x" * 41, "40 characters"),
    ],
)
def test_parse_setting_rejects(text, message):
    with pytest.raises(ValueError, match=message):
        fleet.parse_setting(text, PARAMS, FRAME_SIZES)


def test_tune_presets_are_valid_settings():
    for name, overrides in fleet.TUNE_PRESETS.items():
        fleet.normalise_overrides(overrides, PARAMS, FRAME_SIZES)  # raises on a bad preset
        assert re.fullmatch(r"[a-z0-9_]+", name)  # used in MQTT topics and file names


# --- payloads -------------------------------------------------------------------------


def test_ota_command_carries_hash_and_size():
    release = {"version": "1.2.0", "sha256": "a" * 64, "size_bytes": 1234, "storage_path": "1.2.0/firmware.bin"}
    assert fleet.ota_command(release, "https://x/signed") == {
        "cmd": "ota",
        "url": "https://x/signed",
        "sha256": "a" * 64,
        "size": 1234,
        "version": "1.2.0",
    }


def test_capture_command_only_sends_overrides_when_given():
    assert "overrides" not in fleet.capture_command("t", None, "server")
    assert fleet.capture_command("t", {"flash": 1}, "mqtt")["overrides"] == {"flash": 1}


def test_version_pattern():
    assert fleet.VERSION_RE.match("1.2.3") and fleet.VERSION_RE.match("1.2.3-beta.1")
    assert not fleet.VERSION_RE.match("v1.2") and not fleet.VERSION_RE.match("1.2")


# --- Supabase -------------------------------------------------------------------------


class FakeSupabase:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.configs: dict[str, dict] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/storage/v1/object/sign/firmware/"):
            return httpx.Response(200, json={"signedURL": "/object/sign/firmware/1.2.0/firmware.bin?token=abc"})
        if path == "/rest/v1/device_config" and request.method == "POST":
            body = json.loads(request.content)
            self.configs[body["device_id"]] = body["config"]
            return httpx.Response(201)
        if path == "/rest/v1/device_config":
            device = request.url.params["device_id"].removeprefix("eq.")
            rows = [{"config": self.configs[device]}] if device in self.configs else []
            return httpx.Response(200, json=rows)
        if path == "/rest/v1/firmware_releases" and request.method == "GET":
            return httpx.Response(404, json={"code": "PGRST205", "message": "table not found"})
        return httpx.Response(200, json={})


@pytest.fixture
def fake():
    return FakeSupabase()


@pytest.fixture
def supabase(fake):
    client = httpx.Client(base_url="https://example.supabase.co", transport=httpx.MockTransport(fake.handler))
    return fleet.Supabase(client, "service-key")


def test_signed_url_is_absolute(supabase):
    url = supabase.signed_url("1.2.0/firmware.bin")
    assert url == "https://example.supabase.co/storage/v1/object/sign/firmware/1.2.0/firmware.bin?token=abc"


def test_device_config_round_trip(supabase, fake):
    assert supabase.get_device_config("IV-CAM-01") == {}
    supabase.save_device_config("IV-CAM-01", {"contrast": 1})
    assert supabase.get_device_config("IV-CAM-01") == {"contrast": 1}
    upsert = next(r for r in fake.requests if r.method == "POST")
    assert upsert.headers["Prefer"] == "resolution=merge-duplicates"
    assert upsert.headers["Authorization"] == "Bearer service-key"


def test_missing_tables_point_at_the_migration(supabase):
    with pytest.raises(RuntimeError, match="20261109120000_firmware_ota.sql"):
        supabase.get_release("1.0.0")
