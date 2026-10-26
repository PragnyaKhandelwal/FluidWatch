"""Download the ESP32-CAM training dataset from Supabase.

Every frame the backend receives is archived to the private `training-frames`
bucket, with one `training_frames` row per image (labels included). This
pulls both down for training:

    pip install httpx
    python ml-model-training/fetch_dataset.py --email you@example.com
    python ml-model-training/fetch_dataset.py --email you@example.com --session run-03 --labelled-only

Sign in with your own FluidWatch account - it needs the admin role (an admin
can grant it from the dashboard's /admin page). The project URL and public
key are read from frontend/.env. Re-running only downloads new images.

Output: <out>/images/<storage path> and <out>/labels.csv.
"""

import argparse
import csv
import getpass
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "training-frames"
PAGE = 1000
COLUMNS = [
    "id", "storage_path", "device_id", "captured_at", "bytes", "width", "height",
    "predicted_fill_percent", "predicted_class", "label_fill_percent", "label_yaw_deg",
    "session", "notes",
]


def read_frontend_env() -> dict[str, str]:
    env = {}
    path = ROOT / "frontend" / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"')
    return env


def sign_in(client: httpx.Client, anon_key: str, email: str, password: str) -> str:
    res = client.post(
        "/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": anon_key},
        json={"email": email, "password": password},
    )
    if res.is_error:
        sys.exit(f"Sign-in failed ({res.status_code}): {res.json().get('error_description', res.text)}")
    return res.json()["access_token"]


def fetch_rows(client: httpx.Client, headers: dict, session: str | None, labelled_only: bool) -> list[dict]:
    params = {"select": ",".join(COLUMNS), "order": "id.asc"}
    if session:
        params["session"] = f"eq.{session}"
    if labelled_only:
        params["label_fill_percent"] = "not.is.null"
    rows: list[dict] = []
    while True:
        res = client.get(
            "/rest/v1/training_frames",
            params=params,
            headers={**headers, "Range-Unit": "items", "Range": f"{len(rows)}-{len(rows) + PAGE - 1}"},
        )
        res.raise_for_status()
        page = res.json()
        rows.extend(page)
        if len(page) < PAGE:
            return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", help="Your FluidWatch (admin) account email")
    parser.add_argument("--out", default=str(ROOT / "ml-model-training" / "dataset"), help="Output folder")
    parser.add_argument("--session", help="Only this capture session")
    parser.add_argument("--labelled-only", action="store_true", help="Skip frames without a fill label")
    args = parser.parse_args()

    env = read_frontend_env()
    url = os.environ.get("SUPABASE_URL") or env.get("VITE_SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or env.get("VITE_SUPABASE_PUBLISHABLE_KEY")
    if not url or not anon_key:
        sys.exit("Supabase URL/key not found - set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY, or keep frontend/.env.")

    out = Path(args.out)
    images = out / "images"

    with httpx.Client(base_url=url.rstrip("/"), timeout=60.0) as client:
        email = args.email or input("FluidWatch email: ")
        token = sign_in(client, anon_key, email, getpass.getpass("Password: "))
        headers = {"apikey": anon_key, "Authorization": f"Bearer {token}"}

        rows = fetch_rows(client, headers, args.session, args.labelled_only)
        if not rows:
            sys.exit("No frames visible. Either none are archived yet, or this account isn't an admin.")

        downloaded = skipped = 0
        for i, row in enumerate(rows, 1):
            target = images / row["storage_path"]
            if target.exists() and target.stat().st_size == row["bytes"]:
                skipped += 1
                continue
            res = client.get(f"/storage/v1/object/authenticated/{BUCKET}/{row['storage_path']}", headers=headers)
            res.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(res.content)
            downloaded += 1
            print(f"\r{i}/{len(rows)} images", end="", flush=True)

    out.mkdir(parents=True, exist_ok=True)
    with (out / "labels.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    total_mb = sum(r["bytes"] for r in rows) / 1_048_576
    print(f"\n{len(rows)} frames ({total_mb:.1f} MB): {downloaded} downloaded, {skipped} already present.")
    print(f"Images: {images}\nLabels: {out / 'labels.csv'}")


if __name__ == "__main__":
    main()
