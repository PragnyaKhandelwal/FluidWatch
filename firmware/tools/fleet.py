"""Remote control for FluidWatch ESP32-CAM units - no programmer needed after
the first serial flash. Commands go over MQTT (HiveMQ Cloud); Supabase keeps
the firmware images, their release history and each unit's settings.

    pip install -r firmware/tools/requirements.txt

    python firmware/tools/fleet.py params                     # every setting, range, default, meaning
    python firmware/tools/fleet.py status                     # which units are online, health
    python firmware/tools/fleet.py watch IV-CAM-01            # live events from one unit
    python firmware/tools/fleet.py config IV-CAM-01           # show its saved settings
    python firmware/tools/fleet.py config IV-CAM-01 frame_size=SVGA contrast=1 bed_label="Bed 12"
    python firmware/tools/fleet.py config IV-CAM-01 --reset   # back to firmware defaults
    python firmware/tools/fleet.py capture IV-CAM-01 --out shot.jpg
    python firmware/tools/fleet.py tune IV-CAM-01             # one photo per preset, to compare
    python firmware/tools/fleet.py report IV-CAM-01           # what the sensor is really using
    python firmware/tools/fleet.py release 1.1.0 --notes "camera sleeps between photos"
    python firmware/tools/fleet.py ota IV-CAM-01 1.1.0        # install that release remotely
    python firmware/tools/fleet.py reboot IV-CAM-01

Credentials (never committed): MQTT_HOST, MQTT_PORT, MQTT_USERNAME,
MQTT_PASSWORD from the environment or firmware/tools/.env; SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY from the environment or server/.env.
"""

import argparse
import hashlib
import json
import os
import queue
import re
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
FIRMWARE_DIR = ROOT / "firmware" / "esp32-cam"
CONFIG_CPP = FIRMWARE_DIR / "src" / "capture_config.cpp"
BUILT_IMAGE = FIRMWARE_DIR / ".pio" / "build" / "esp32cam" / "firmware.bin"
BUCKET = "firmware"
LABEL_KEYS = ("bed_label", "patient_name", "fluid_label")
MAX_LABEL_LEN = 40
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")
SIGNED_URL_TTL_S = 3600

# One-off capture variants for `tune`: each changes a few settings from the
# unit's saved config for a single photo, delivered over MQTT so tuning shots
# never land in the training dataset or the dashboard.
TUNE_PRESETS: dict[str, dict] = {
    "baseline": {},
    "vga_q10": {"frame_size": "VGA", "jpeg_quality": 10},
    "svga_q10": {"frame_size": "SVGA", "jpeg_quality": 10},
    "xga_q10": {"frame_size": "XGA", "jpeg_quality": 10},
    "flash": {"flash": 1},
    "ae_minus1": {"ae_level": -1},
    "ae_plus1": {"ae_level": 1},
    "contrast_plus1": {"contrast": 1},
    "contrast_plus2": {"contrast": 2},
    "sharpness_plus1": {"sharpness": 1},
    "gain_16x": {"gainceiling": 3},
    "gain_4x": {"gainceiling": 1},
    "wb_office": {"wb_mode": 3},
    "warmup_10": {"warmup_frames": 10},
    "fixed_exposure": {"aec": 0, "aec_value": 400, "agc": 0, "agc_gain": 4},
}


# --- settings table (single source of truth: capture_config.cpp) --------------


def load_params(path: Path = CONFIG_CPP) -> list[dict]:
    """The firmware's kParams table: [{key, min, max, default, help}, ...]."""
    text = path.read_text(encoding="utf-8")
    rows = re.findall(r'\{"(\w+)",\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*"([^"]*)"\}', text)
    return [{"key": k, "min": int(lo), "max": int(hi), "default": int(d), "help": h} for k, lo, hi, d, h in rows]


def load_frame_sizes(path: Path = CONFIG_CPP) -> list[str]:
    block = re.search(r"kFrameSizeNames\[\]\s*=\s*\{(.*?)\};", path.read_text(encoding="utf-8"), re.S)
    return re.findall(r'"(\w+)"', block.group(1)) if block else []


def parse_setting(text: str, params: list[dict], frame_sizes: list[str]) -> tuple[str, object]:
    """'key=value' -> (key, validated value), with the same rules as the firmware."""
    if "=" not in text:
        raise ValueError(f"expected key=value, got {text!r}")
    key, raw = (part.strip() for part in text.split("=", 1))
    if key in LABEL_KEYS:
        if len(raw) > MAX_LABEL_LEN:
            raise ValueError(f"{key}: at most {MAX_LABEL_LEN} characters")
        return key, raw

    spec = next((p for p in params if p["key"] == key), None)
    if spec is None:
        raise ValueError(f"unknown setting {key!r} (see `fleet.py params`)")
    lowered = raw.lower()
    if lowered in ("on", "true", "yes"):
        value = 1
    elif lowered in ("off", "false", "no"):
        value = 0
    elif key == "frame_size" and raw.upper() in frame_sizes:
        value = frame_sizes.index(raw.upper())
    else:
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"{key}: {raw!r} is not a number") from None
    if not spec["min"] <= value <= spec["max"]:
        raise ValueError(f"{key}: {value} outside {spec['min']}..{spec['max']}")
    if key == "cpu_mhz" and value not in (80, 160, 240):
        raise ValueError("cpu_mhz: must be 80, 160 or 240")
    return key, value


def firmware_defaults(params: list[dict], config_h: Path = FIRMWARE_DIR / "include" / "config.h") -> dict:
    """Every setting at the value a freshly flashed unit uses."""
    defaults: dict = {p["key"]: p["default"] for p in params}
    text = config_h.read_text(encoding="utf-8")
    for key in LABEL_KEYS:
        match = re.search(rf'#define\s+{key.upper()}\s+"([^"]*)"', text)
        if match:
            defaults[key] = match.group(1)
    return defaults


def normalise_overrides(overrides: dict, params: list[dict], frame_sizes: list[str]) -> dict:
    return dict(parse_setting(f"{k}={v}", params, frame_sizes) for k, v in overrides.items())


# --- credentials ---------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def setting(name: str, *files: Path, default: str = "") -> str:
    if os.environ.get(name):
        return os.environ[name]
    for f in files:
        value = read_env_file(f).get(name)
        if value:
            return value
    return default


# --- Supabase -------------------------------------------------------------------


class Supabase:
    """Service-role access to the firmware bucket and the two tables."""

    def __init__(self, client: httpx.Client, key: str):
        self.client = client
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    @classmethod
    def from_env(cls) -> "Supabase":
        env_file = ROOT / "server" / ".env"
        url = setting("SUPABASE_URL", env_file)
        key = setting("SUPABASE_SERVICE_ROLE_KEY", env_file)
        if not url or not key:
            sys.exit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not found (env or server/.env).")
        return cls(httpx.Client(base_url=url.rstrip("/"), timeout=60.0), key)

    def _check(self, res: httpx.Response, what: str) -> httpx.Response:
        if res.is_error:
            hint = ""
            if res.status_code == 404 or "PGRST205" in res.text:
                hint = " - run supabase/migrations/20261109120000_firmware_ota.sql in the SQL editor first"
            raise RuntimeError(f"{what} failed ({res.status_code}): {res.text[:300]}{hint}")
        return res

    def get_device_config(self, device: str) -> dict:
        res = self._check(
            self.client.get(
                "/rest/v1/device_config",
                params={"device_id": f"eq.{device}", "select": "config"},
                headers=self.headers,
            ),
            "reading device_config",
        )
        rows = res.json()
        return rows[0]["config"] if rows else {}

    def save_device_config(self, device: str, config: dict) -> None:
        self._check(
            self.client.post(
                "/rest/v1/device_config",
                json={"device_id": device, "config": config, "updated_at": datetime.now(UTC).isoformat()},
                headers={**self.headers, "Prefer": "resolution=merge-duplicates"},
            ),
            "saving device_config",
        )

    def upload_image(self, path: str, data: bytes) -> None:
        self._check(
            self.client.post(
                f"/storage/v1/object/{BUCKET}/{path}",
                content=data,
                headers={**self.headers, "Content-Type": "application/octet-stream"},
            ),
            "uploading firmware image",
        )

    def add_release(self, row: dict) -> None:
        self._check(
            self.client.post("/rest/v1/firmware_releases", json=row, headers=self.headers),
            "recording release",
        )

    def get_release(self, version: str) -> dict | None:
        res = self._check(
            self.client.get(
                "/rest/v1/firmware_releases",
                params={"version": f"eq.{version}", "select": "*"},
                headers=self.headers,
            ),
            "reading firmware_releases",
        )
        rows = res.json()
        return rows[0] if rows else None

    def list_releases(self) -> list[dict]:
        res = self._check(
            self.client.get(
                "/rest/v1/firmware_releases",
                params={"select": "*", "order": "created_at.desc"},
                headers=self.headers,
            ),
            "reading firmware_releases",
        )
        return res.json()

    def signed_url(self, path: str, expires_s: int = SIGNED_URL_TTL_S) -> str:
        res = self._check(
            self.client.post(
                f"/storage/v1/object/sign/{BUCKET}/{path}", json={"expiresIn": expires_s}, headers=self.headers
            ),
            "signing download URL",
        )
        return f"{self.client.base_url}/storage/v1{res.json()['signedURL']}"


# --- MQTT -------------------------------------------------------------------------


def topic(device: str, sub: str) -> str:
    return f"fluidwatch/{device}/{sub}"


class Broker:
    """Thin paho-mqtt wrapper: publish and wait for matching messages."""

    def __init__(self):
        import paho.mqtt.client as mqtt

        env_file = ROOT / "firmware" / "tools" / ".env"
        host = setting("MQTT_HOST", env_file)
        if not host:
            sys.exit("MQTT_HOST not set (env or firmware/tools/.env) - see the README's Remote management section.")
        port = int(setting("MQTT_PORT", env_file, default="8883"))
        self.messages: queue.Queue = queue.Queue()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fleet-{os.getpid()}")
        self.client.username_pw_set(setting("MQTT_USERNAME", env_file), setting("MQTT_PASSWORD", env_file))
        self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_message = lambda _c, _u, msg: self.messages.put((msg.topic, msg.payload, msg.retain))
        connected = queue.Queue()
        self.client.on_connect = lambda _c, _u, _f, reason, _p: connected.put(reason)
        self.client.connect(host, port, keepalive=60)
        self.client.loop_start()
        try:
            reason = connected.get(timeout=15)
        except queue.Empty:
            sys.exit(f"No answer from MQTT broker {host}:{port}.")
        if reason.is_failure:
            sys.exit(f"MQTT broker refused the connection: {reason}")

    def subscribe(self, pattern: str) -> None:
        self.client.subscribe(pattern, qos=1)

    def publish(self, topic_name: str, payload, retain: bool = False) -> None:
        body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        self.client.publish(topic_name, body, qos=1, retain=retain).wait_for_publish(timeout=15)

    def wait_for(self, predicate, timeout: float):
        """First (topic, payload) for which predicate(topic, payload) is true, or None."""
        deadline = time.time() + timeout
        while (remaining := deadline - time.time()) > 0:
            try:
                t, payload, _retain = self.messages.get(timeout=remaining)
            except queue.Empty:
                return None
            if predicate(t, payload):
                return t, payload
        return None

    def wait_event(self, device: str, types: set[str], timeout: float) -> dict | None:
        def match(t: str, payload: bytes) -> bool:
            if t != topic(device, "event"):
                return False
            try:
                return json.loads(payload).get("type") in types
            except ValueError:
                return False

        hit = self.wait_for(match, timeout)
        return json.loads(hit[1]) if hit else None

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


# --- payloads ---------------------------------------------------------------------


def ota_command(release: dict, url: str) -> dict:
    return {
        "cmd": "ota",
        "url": url,
        "sha256": release["sha256"],
        "size": release["size_bytes"],
        "version": release["version"],
    }


def capture_command(tag: str, overrides: dict | None, deliver: str) -> dict:
    cmd: dict = {"cmd": "capture", "tag": tag, "deliver": deliver}
    if overrides:
        cmd["overrides"] = overrides
    return cmd


# --- commands ---------------------------------------------------------------------


def cmd_params(args) -> None:
    params = load_params()
    if args.markdown:
        print("| Setting | Range | Default | What it does |\n|---|---|---|---|")
        for p in params:
            print(f"| `{p['key']}` | {p['min']}..{p['max']} | {p['default']} | {p['help']} |")
        return
    width = max(len(p["key"]) for p in params)
    for p in params:
        rng = f"{p['min']}..{p['max']}"
        print(f"{p['key']:<{width}}  {rng:>10}  default {p['default']:<5} {p['help']}")
    print(f"\nframe_size names: {', '.join(load_frame_sizes())}")
    print(f"text settings: {', '.join(LABEL_KEYS)} (max {MAX_LABEL_LEN} chars)")


def cmd_status(args) -> None:
    broker = Broker()
    broker.subscribe(topic(args.device or "+", "status"))
    seen: dict[str, dict] = {}
    deadline = time.time() + args.seconds
    while (remaining := deadline - time.time()) > 0:
        hit = broker.wait_for(lambda t, _p: t.endswith("/status"), remaining)
        if hit is None:
            break
        try:
            seen[hit[0].split("/")[1]] = json.loads(hit[1])
        except ValueError:
            continue
    broker.close()
    if not seen:
        print("No units have reported yet.")
    for device, st in sorted(seen.items()):
        if not st.get("online"):
            print(f"{device}: OFFLINE")
            continue
        last = st.get("last_capture", {})
        print(
            f"{device}: online, fw {st.get('fw')}, up {st.get('uptime_s', 0) // 60} min, "
            f"wifi {st.get('rssi')} dBm, cpu {st.get('cpu_mhz')} MHz, chip ~{st.get('chip_temp_c', 0):.0f} C, "
            f"heap {st.get('heap_free', 0) // 1024} KB, every {st.get('interval_s')} s, "
            f"last photo {last.get('ago_s', '?')} s ago (HTTP {last.get('http', '?')})"
            + (f", OTA: {st['ota']}" if st.get("ota") else "")
        )


def cmd_watch(args) -> None:
    broker = Broker()
    broker.subscribe(topic(args.device, "event"))
    broker.subscribe(topic(args.device, "status"))
    print(f"Watching {args.device} (Ctrl+C to stop)...")
    try:
        while True:
            hit = broker.wait_for(lambda _t, _p: True, 3600)
            if hit:
                print(f"{time.strftime('%H:%M:%S')} {hit[0].split('/')[-1]}: {hit[1].decode(errors='replace')}")
    except KeyboardInterrupt:
        pass
    broker.close()


def cmd_config(args) -> None:
    params, frame_sizes = load_params(), load_frame_sizes()
    changes = dict(parse_setting(s, params, frame_sizes) for s in args.settings)
    supabase = Supabase.from_env()
    current = {} if args.reset else supabase.get_device_config(args.device)

    if not changes and not args.reset:
        print(json.dumps(current, indent=2) if current else f"{args.device} uses the firmware defaults.")
        return

    # Supabase keeps only what differs from the firmware defaults; the device
    # always gets the complete set, so a setting put back to its default (or a
    # --reset) really overrides whatever it had saved before.
    defaults = firmware_defaults(params)
    updated = {k: v for k, v in {**current, **changes}.items() if defaults.get(k) != v}
    to_send = {**defaults, **updated}

    supabase.save_device_config(args.device, updated)
    broker = Broker()
    broker.subscribe(topic(args.device, "event"))
    broker.publish(topic(args.device, "config"), to_send, retain=True)
    print(f"Saved to Supabase and published to {args.device}: {json.dumps(to_send)}")
    result = broker.wait_event(args.device, {"config_applied", "config_rejected"}, timeout=20)
    broker.close()
    if result is None:
        print("The unit didn't answer within 20 s - it will apply the settings when it next connects.")
    elif result.get("errors") or result.get("error"):
        print(f"Applied with problems: {result.get('errors') or result.get('error')}")
    else:
        print(
            f"Applied {result.get('applied')} setting(s)."
            + (f" Sensor rejected: {result['unsupported']}" if result.get("unsupported") else "")
        )


def capture_one(broker: Broker, device: str, tag: str, overrides: dict | None, timeout: float = 45) -> tuple:
    """Triggers an MQTT-delivered capture. Returns (jpeg bytes or None, capture event or None)."""
    broker.publish(topic(device, "cmd"), capture_command(tag, overrides, "mqtt"))
    photo_topic = topic(device, f"photo/{tag}")
    jpeg, event = None, None
    deadline = time.time() + timeout
    while (jpeg is None or event is None) and (remaining := deadline - time.time()) > 0:
        hit = broker.wait_for(lambda t, _p: t in (photo_topic, topic(device, "event")), remaining)
        if hit is None:
            break
        if hit[0] == photo_topic:
            jpeg = hit[1]
        else:
            payload = json.loads(hit[1])
            if payload.get("type") == "capture" and payload.get("tag") == tag:
                event = payload
            elif payload.get("type") in ("capture_failed", "cmd_rejected"):
                return None, payload
    return jpeg, event


def cmd_capture(args) -> None:
    broker = Broker()
    broker.subscribe(topic(args.device, "event"))
    if args.to == "server":
        broker.publish(topic(args.device, "cmd"), capture_command("manual", None, "server"))
        result = broker.wait_event(args.device, {"capture", "capture_failed"}, timeout=45)
        print(json.dumps(result, indent=2) if result else "No answer within 45 s.")
    else:
        broker.subscribe(topic(args.device, "photo/#"))
        jpeg, event = capture_one(broker, args.device, "manual", None)
        if jpeg:
            Path(args.out).write_bytes(jpeg)
            print(f"Saved {args.out} ({len(jpeg) // 1024} KB) - {json.dumps(event)}")
        else:
            print(f"No photo: {event}")
    broker.close()


def cmd_tune(args) -> None:
    params, frame_sizes = load_params(), load_frame_sizes()
    presets = TUNE_PRESETS
    if args.only:
        wanted = args.only.split(",")
        unknown = [w for w in wanted if w not in TUNE_PRESETS]
        if unknown:
            sys.exit(f"Unknown preset(s) {unknown}. Choose from: {', '.join(TUNE_PRESETS)}")
        presets = {k: TUNE_PRESETS[k] for k in wanted}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    broker = Broker()
    broker.subscribe(topic(args.device, "event"))
    broker.subscribe(topic(args.device, "photo/#"))
    summary = []
    for name, overrides in presets.items():
        jpeg, event = capture_one(broker, args.device, name, normalise_overrides(overrides, params, frame_sizes))
        if not jpeg:
            print(f"{name:<16} FAILED: {event}")
            continue
        (out / f"{name}.jpg").write_bytes(jpeg)
        row = {"preset": name, "overrides": overrides, "bytes": len(jpeg), **(event or {})}
        summary.append(row)
        print(
            f"{name:<16} {row.get('width')}x{row.get('height')}  {len(jpeg) // 1024:>4} KB  "
            f"{row.get('took_ms', '?')} ms  {overrides}"
        )
    broker.close()
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPhotos in {out} - compare them, then apply the winner with `fleet.py config {args.device} ...`.")


def cmd_report(args) -> None:
    broker = Broker()
    broker.subscribe(topic(args.device, "event"))
    broker.publish(topic(args.device, "cmd"), {"cmd": "report"})
    result = broker.wait_event(args.device, {"report"}, timeout=30)
    broker.close()
    print(json.dumps(result, indent=2) if result else "No answer within 30 s.")


def cmd_reboot(args) -> None:
    broker = Broker()
    broker.publish(topic(args.device, "cmd"), {"cmd": "reboot"})
    broker.close()
    print(f"Reboot sent to {args.device}.")


def cmd_release(args) -> None:
    if not VERSION_RE.match(args.version):
        sys.exit("Version must look like 1.2.3 (optionally 1.2.3-beta.1).")
    if args.bin:
        image = Path(args.bin)
    else:
        pio = os.environ.get("PIO", "pio")
        env = {**os.environ, "PLATFORMIO_BUILD_FLAGS": f'-DFW_VERSION=\\"{args.version}\\"'}
        print(f"Building firmware {args.version}...")
        subprocess.run([pio, "run"], cwd=FIRMWARE_DIR, env=env, check=True)
        image = BUILT_IMAGE
    data = image.read_bytes()
    if args.version.encode() not in data:
        sys.exit(f"{image} doesn't contain version string {args.version} - was it built with FW_VERSION?")

    supabase = Supabase.from_env()
    if supabase.get_release(args.version):
        sys.exit(f"Release {args.version} already exists - releases are immutable, bump the version.")
    path = f"{args.version}/firmware.bin"
    row = {
        "version": args.version,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "storage_path": path,
        "notes": args.notes,
    }
    supabase.upload_image(path, data)
    supabase.add_release(row)
    print(f"Released {args.version}: {len(data) // 1024} KB, sha256 {row['sha256']}")


def cmd_releases(_args) -> None:
    for r in Supabase.from_env().list_releases():
        print(f"{r['version']:<14} {r['created_at'][:16]}  {r['size_bytes'] // 1024} KB  {r.get('notes') or ''}")


def cmd_ota(args) -> None:
    supabase = Supabase.from_env()
    release = supabase.get_release(args.version)
    if release is None:
        sys.exit(f"No release {args.version} - create it with `fleet.py release {args.version}`.")
    url = supabase.signed_url(release["storage_path"])

    broker = Broker()
    broker.subscribe(topic(args.device, "event"))
    broker.subscribe(topic(args.device, "status"))
    broker.publish(topic(args.device, "cmd"), ota_command(release, url))
    print(f"Sent {args.version} to {args.device}; waiting for it to download and verify...")

    deadline = time.time() + args.timeout
    while (remaining := deadline - time.time()) > 0:
        event = broker.wait_event(args.device, {"ota_start", "ota_progress", "ota_installed", "ota_failed"}, remaining)
        if event is None:
            break
        kind = event["type"]
        if kind == "ota_progress":
            print(f"  {event['percent']}%")
        elif kind == "ota_failed":
            broker.close()
            sys.exit(f"Update failed: {event.get('error')} (the unit keeps running its current firmware)")
        elif kind == "ota_installed":
            print("Installed and verified; the unit is rebooting into it...")
            break

    def running_new(t: str, payload: bytes) -> bool:
        if t != topic(args.device, "status"):
            return False
        st = json.loads(payload)
        return st.get("online") and st.get("fw") == args.version

    ok = broker.wait_for(running_new, 180)
    broker.close()
    if ok:
        print(f"{args.device} is online running {args.version}.")
    else:
        print(
            "The unit hasn't come back on the new version yet. If it can't reach the broker within 5 min "
            "it rolls back to the previous firmware by itself - check `fleet.py status` shortly."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("params", help="list every setting")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_params)

    p = sub.add_parser("status", help="online units and their health")
    p.add_argument("device", nargs="?")
    p.add_argument("--seconds", type=float, default=4)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("watch", help="stream a unit's events")
    p.add_argument("device")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("config", help="show or change a unit's settings")
    p.add_argument("device")
    p.add_argument("settings", nargs="*", help="key=value")
    p.add_argument("--reset", action="store_true", help="back to firmware defaults")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("capture", help="take a photo now")
    p.add_argument("device")
    p.add_argument(
        "--to",
        choices=["mqtt", "server"],
        default="mqtt",
        help="mqtt: save it here (default); server: normal upload to the backend",
    )
    p.add_argument("--out", default="capture.jpg")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("tune", help="one photo per preset, to pick the best settings")
    p.add_argument("device")
    p.add_argument("--only", help=f"comma-separated presets ({', '.join(TUNE_PRESETS)})")
    p.add_argument("--out", default="tune")
    p.set_defaults(func=cmd_tune)

    p = sub.add_parser("report", help="sensor model and live values")
    p.add_argument("device")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("reboot")
    p.add_argument("device")
    p.set_defaults(func=cmd_reboot)

    p = sub.add_parser("release", help="build and publish a firmware version")
    p.add_argument("version")
    p.add_argument("--notes")
    p.add_argument("--bin", help="publish this .bin instead of building")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("releases", help="list published firmware versions")
    p.set_defaults(func=cmd_releases)

    p = sub.add_parser("ota", help="install a released version on a unit")
    p.add_argument("device")
    p.add_argument("version")
    p.add_argument("--timeout", type=float, default=300)
    p.set_defaults(func=cmd_ota)

    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, RuntimeError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
