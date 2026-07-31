#!/usr/bin/env python3
"""Pull, normalize, and optionally publish Sure Pet telemetry.

No credentials, household identifiers, endpoints, or upload tokens are stored
in this project. Supply them through environment variables or a local .env file
that is deliberately excluded from version control.

The workflow intentionally has three separate outputs:

    Sure Pet API -> raw CSV -> normalized JSON -> optional dashboard endpoint

Raw CSV preserves the source-oriented event categories. Normalized JSON groups
them into a small, portable telemetry vocabulary that a dashboard can consume.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


# ---------------------------------------------------------------------------
# SHARED CONTRACTS
# Keep the API address and the raw CSV shape in one visible place. These values
# are shared by the pull and normalization stages, but contain no household
# identifiers, credentials, or upload settings.
# ---------------------------------------------------------------------------
API_ROOT = "https://app.api.surehub.io/api"
RAW_COLUMNS = [
    "recordedAt", "subjectId", "subjectName", "category", "action",
    "quantity", "unit", "occurredAt", "durationSeconds", "deviceId",
    "deviceName", "context", "sourceEndpoint", "attribution",
]

# These labels are this project's documented interpretation of the known
# numeric context values. The source number is always retained separately in
# ``context`` so a consumer can audit or apply a different interpretation.
CONTEXT_ATTRIBUTIONS = {
    1: "pet",
    3: "owner",
    5: "food_addition",
    6: "system_event",
}


def utc_now() -> str:
    """Return a portable UTC timestamp for records created by this tool."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def as_number(value: Any) -> float | None:
    """Convert optional source text to a number without crashing on blanks."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def attribution_for_context(context: Any) -> str:
    """Return a documented label for a known source context code.

    PSEUDOCODE:
    1. Try to read the source value as an integer without changing it.
    2. Return this project's label when that integer is known.
    3. Keep unfamiliar or missing values visibly ``unknown`` rather than
       guessing what they mean.
    """
    try:
        return CONTEXT_ATTRIBUTIONS.get(int(context), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def event_id(event: dict[str, Any]) -> str:
    """Build a deterministic ID so repeated pulls describe the same event."""
    identity = "|".join(str(event.get(key, "")) for key in (
        "occurredAt", "subjectId", "category", "action", "quantity", "deviceId", "context",
    ))
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]


def required_environment(names: Iterable[str]) -> dict[str, str]:
    """Read secret runtime settings without ever printing their values."""
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    return values


def load_local_dotenv(path: Path | None = None, environment: dict[str, str] | None = None) -> None:
    """Load simple ``KEY=value`` settings from the local, ignored ``.env`` file.

    PSEUDOCODE:
    1. Look beside this script for an optional `.env` file.
    2. Read only valid environment-variable assignments; ignore blank lines and
       comments.
    3. Remove one matching pair of surrounding quotes from a value.
    4. Do not overwrite a variable already supplied by the operating system.

    This small parser avoids making a configuration file executable shell code.
    It intentionally supports the simple `.env` format documented in this
    project rather than attempting to reproduce every dotenv convention.
    """
    source = path or Path(__file__).resolve().with_name(".env")
    target = environment if environment is not None else os.environ
    if not source.is_file():
        return
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        target.setdefault(key, value)


# ---------------------------------------------------------------------------
# SURE PET API ACCESS
# This class fetches source data. It does not decide what the data means.
# ---------------------------------------------------------------------------
class SurePetClient:
    """Minimal, credential-aware client for the Sure Pet API.

    It has two source surfaces:

    * Aggregate pet reports (`/report/.../aggregate`) return pet-associated
      feeding, drinking, and movement datapoints.
    * Notifications (`/notification`) return human-facing alerts. This project
      treats qualifying water-removal alerts as *ambient* observations rather
      than attributing them to a particular pet.
    """
    def __init__(self, email: str, password: str, device_id: str) -> None:
        self.email = email
        self.password = password
        self.device_id = device_id

    def _request(self, method: str, path: str, *, token: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Make one checked API request and return a JSON object."""
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = requests.request(method, f"{API_ROOT}{path}", headers=headers, timeout=30, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected response from {path}")
        return payload

    def login(self) -> str:
        """Authenticate once and return the short-lived API token.

        Credentials are read by ``pull()`` from environment variables. They are
        never written to CSV, JSON, logs, or source control.
        """
        response = self._request(
            "POST",
            "/auth/login",
            headers={"Content-Type": "application/json"},
            json={"email_address": self.email, "password": self.password, "device_id": self.device_id},
        )
        token = response.get("data", {}).get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Sure Pet login did not return a token.")
        return token

    def pets(self, token: str) -> list[dict[str, Any]]:
        """Return the household's pets, including IDs needed for reports."""
        data = self._request("GET", "/pet", token=token).get("data", [])
        return [pet for pet in data if isinstance(pet, dict)] if isinstance(data, list) else []

    def aggregate_report(self, token: str, household_id: str | int, pet_id: str | int, start: str, end: str) -> dict[str, Any]:
        """Fetch one pet's aggregate report for the requested UTC date range.

        Expected source sections include:

        * ``feeding`` — feeder weight-change datapoints
        * ``drinking`` — fountain weight-change datapoints
        * ``movement`` — pet-flap / passage datapoints

        The API response is preserved as an input to ``report_events()``; this
        method does not decide what any measurement means.
        """
        response = self._request(
            "GET",
            f"/report/household/{household_id}/pet/{pet_id}/aggregate",
            token=token,
            params={"from": start, "to": end},
        )
        data = response.get("data", {})
        return data if isinstance(data, dict) else {}

    def notifications(self, token: str, pages: int, page_size: int, delay_seconds: float) -> list[dict[str, Any]]:
        """Fetch a bounded, throttled slice of human-facing notifications.

        The method requests one page at a time, stops when the API has no more
        records, and pauses between pages. Notifications can describe a water
        removal or another household event but may not identify a pet; their
        interpretation happens later.
        """
        collected: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            response = self._request("GET", "/notification", token=token, params={"page": page, "page_size": page_size})
            batch = response.get("data", [])
            if not isinstance(batch, list) or not batch:
                break
            collected.extend(note for note in batch if isinstance(note, dict))
            if page < pages:
                time.sleep(delay_seconds)
        return collected


# ---------------------------------------------------------------------------
# SOURCE INTERPRETATION
# Convert the two Sure Pet source surfaces into explicitly labeled raw events.
# ---------------------------------------------------------------------------
def report_events(report: dict[str, Any], pet: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
    """Translate aggregate-report datapoints into source-oriented CSV rows.

    For each supported report section, preserve the supplied pet, device,
    timestamp, duration, and numeric context. Known context values receive a
    documented attribution label; unfamiliar values remain ``unknown``. This
    function deliberately does not calculate food-consumption policy.

    The three report sections are represented as follows:

    * Feeding is recorded as a ``food_change`` measured in grams.
    * Drinking is recorded as a ``fountain_change`` measured in grams. It is a
      device measurement, not automatically proof of hydration consumed.
    * Movement is recorded as an ``access`` ``passage`` event. It has no
      measurement, while source context can retain directional information.
    """
    result: list[dict[str, Any]] = []
    category_map = {
        "feeding": ("feeding", "food_change", "g"),
        "drinking": ("hydration", "fountain_change", "g"),
        "movement": ("access", "passage", ""),
    }
    for report_type, section in report.items():
        if not isinstance(section, dict) or report_type not in category_map:
            continue
        category, action, unit = category_map[report_type]
        records = section.get("datapoints", [])
        if not isinstance(records, list):
            continue
        for entry in records:
            if not isinstance(entry, dict):
                continue
            weights = entry.get("weights", [])
            raw_quantity = weights[0].get("change") if isinstance(weights, list) and weights and isinstance(weights[0], dict) else None
            quantity = as_number(raw_quantity)
            context = entry.get("context")
            result.append({
                "recordedAt": utc_now(),
                "subjectId": pet.get("id", ""),
                "subjectName": pet.get("name", "Unknown pet"),
                "category": category,
                "action": action,
                "quantity": abs(quantity) if quantity is not None else "",
                "unit": unit if quantity is not None else "",
                "occurredAt": entry.get("to", ""),
                "durationSeconds": entry.get("duration", ""),
                "deviceId": entry.get("device_id", ""),
                "deviceName": "",
                "context": context if context is not None else "",
                "sourceEndpoint": endpoint,
                "attribution": attribution_for_context(context),
            })
    return result


def notification_events(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate qualifying notification alerts into ambient CSV rows.

    This project recognizes one known water-removal alert type, extracts the
    stated volume and device wording, and creates an ``ambient``
    ``water_removed`` event with no pet subject. The amount comes from alert
    text and describes an observed removal, not a pet's water consumption.
    """
    result: list[dict[str, Any]] = []
    for note in notes:
        if note.get("type") != 34:
            continue
        text = str(note.get("text", ""))
        match = re.match(r"^(\d+)", text)
        device_name = text.split(" from ", 1)[1].strip() if " from " in text else text
        result.append({
            "recordedAt": utc_now(), "subjectId": "", "subjectName": "",
            "category": "ambient", "action": "water_removed",
            "quantity": match.group(1) if match else "", "unit": "ml" if match else "",
            "occurredAt": note.get("created_at", ""), "durationSeconds": "", "deviceId": "",
            "deviceName": device_name, "context": "", "sourceEndpoint": "/notification",
            "attribution": "ambient",
        })
    return result


# ---------------------------------------------------------------------------
# LOCAL DATA PRODUCTS
# Write a reviewable raw checkpoint, then make a stable dashboard payload.
# ---------------------------------------------------------------------------
def write_csv(path: Path, events: list[dict[str, Any]]) -> None:
    """Write the source-oriented checkpoint used by inspection and normalization.

    The file contains telemetry rows only—never credentials or API tokens.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=RAW_COLUMNS)
        writer.writeheader()
        writer.writerows(events)


def normalize_csv(csv_path: Path) -> dict[str, Any]:
    """Convert source-oriented CSV into portable telemetry JSON (schema v2).

    Rows without a timestamp, category, or action are skipped. All other rows
    are given consistent subject, device, measurement, and source fields;
    ambient events remain subject-less. Stable IDs and chronological sorting
    make the resulting file predictable for a dashboard.
    """
    with csv_path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    events: list[dict[str, Any]] = []
    for row in rows:
        occurred_at = row.get("occurredAt", "")
        category = row.get("category", "")
        action = row.get("action", "")
        if not occurred_at or not category or not action:
            continue
        quantity = as_number(row.get("quantity"))
        source_context = row.get("context")
        recorded_attribution = row.get("attribution")
        event = {
            "occurredAt": occurred_at,
            "category": category,
            "action": action,
            "subject": {"id": row["subjectId"], "name": row["subjectName"]} if row.get("subjectId") or row.get("subjectName") else None,
            # A legacy/raw CSV may not yet contain the documented label. Infer
            # it from its preserved context code when it is blank or unknown.
            "attribution": recorded_attribution if recorded_attribution and recorded_attribution != "unknown" else attribution_for_context(source_context),
            "device": {"id": row["deviceId"], "name": row["deviceName"]} if row.get("deviceId") or row.get("deviceName") else None,
            "context": source_context or None,
            "durationSeconds": int(as_number(row.get("durationSeconds")) or 0) or None,
            "measurement": {"value": quantity, "unit": row.get("unit")} if quantity is not None and row.get("unit") else None,
            "source": {"endpoint": row.get("sourceEndpoint") or "unknown"},
        }
        event["id"] = event_id({**row, "occurredAt": occurred_at, "category": category, "action": action})
        events.append(event)
    events.sort(key=lambda event: event["occurredAt"])
    return {"schemaVersion": 2, "generatedAt": utc_now(), "events": events}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist normalized JSON as a human-reviewable dashboard data snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def publish(payload: dict[str, Any], url: str, token: str) -> dict[str, Any]:
    """Send normalized telemetry to a compatible, protected ingest endpoint.

    The secret is sent in a dedicated request header. This public repository
    does not provide a hosted endpoint; a deployment must explicitly accept
    this schema-v2 payload.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"X-Telemetry-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Upload failed ({error.code}): {error.read().decode(errors='replace')}") from error
    if not isinstance(result, dict):
        raise RuntimeError("Upload returned an unexpected response.")
    return result


# ---------------------------------------------------------------------------
# LIVE WORKFLOW AND COMMAND LINE
# Each command can stop at CSV or JSON, so review and publishing stay separate.
# ---------------------------------------------------------------------------
def pull(args: argparse.Namespace) -> Path:
    """Run the live Sure Pet pull and write its raw CSV checkpoint.

    It reads credentials from the environment, fetches aggregate reports for
    each returned pet, optionally fetches notifications, and writes one raw
    CSV without conflating the two source surfaces.
    """
    settings = required_environment(["SUREPET_EMAIL", "SUREPET_PASSWORD", "SUREPET_DEVICE_ID"])
    client = SurePetClient(settings["SUREPET_EMAIL"], settings["SUREPET_PASSWORD"], settings["SUREPET_DEVICE_ID"])
    print("Logging in to Sure Pet…")
    token = client.login()
    pets = client.pets(token)
    if not pets:
        raise RuntimeError("No pets were returned by Sure Pet.")
    household_id = pets[0].get("household_id")
    if household_id is None:
        raise RuntimeError("Sure Pet did not return a household ID.")
    events: list[dict[str, Any]] = []
    for pet in pets:
        pet_id = pet.get("id")
        if pet_id is None:
            continue
        print(f"Pulling telemetry for {pet.get('name', 'pet')}…")
        endpoint = f"/report/household/{household_id}/pet/{pet_id}/aggregate"
        events.extend(report_events(client.aggregate_report(token, household_id, pet_id, args.start, args.end), pet, endpoint))
    if not args.skip_notifications:
        print("Pulling notification events…")
        events.extend(notification_events(client.notifications(token, args.notification_pages, args.notification_page_size, args.notification_delay)))
    output = Path(args.csv)
    write_csv(output, events)
    print(f"Wrote {len(events)} raw telemetry events to {output}")
    return output


def add_date_arguments(parser: argparse.ArgumentParser) -> None:
    """Add shared live-pull controls to ``pull`` and ``sync`` commands."""
    parser.add_argument("--start", required=True, help="Inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat(), help="Inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--csv", default="data/surepet_events.local.csv", help="Raw telemetry CSV output")
    parser.add_argument("--skip-notifications", action="store_true")
    parser.add_argument("--notification-pages", type=int, default=25)
    parser.add_argument("--notification-page-size", type=int, default=25)
    parser.add_argument("--notification-delay", type=float, default=2.0)


def parser() -> argparse.ArgumentParser:
    """Define a small CLI whose stages can run independently.

    * ``pull`` fetches live source data into CSV.
    * ``normalize`` makes JSON from any compatible CSV, including demo data.
    * ``publish`` uploads existing normalized JSON.
    * ``sync`` chains pull + normalize and publishes only with ``--publish``.
    """
    root = argparse.ArgumentParser(description="Sure Pet telemetry pull, normalization, and publishing")
    commands = root.add_subparsers(dest="command", required=True)
    pull_parser = commands.add_parser("pull", help="Fetch raw Sure Pet telemetry into CSV")
    add_date_arguments(pull_parser)
    normalize_parser = commands.add_parser("normalize", help="Normalize raw CSV into portable telemetry JSON")
    normalize_parser.add_argument("--csv", required=True)
    normalize_parser.add_argument("--json", required=True)
    publish_parser = commands.add_parser("publish", help="Upload normalized telemetry JSON")
    publish_parser.add_argument("--json", required=True)
    publish_parser.add_argument("--ingest-url", default=os.environ.get("TELEMETRY_INGEST_URL", ""))
    publish_parser.add_argument("--ingest-token", default=os.environ.get("TELEMETRY_INGEST_TOKEN", ""))
    sync_parser = commands.add_parser("sync", help="Pull, normalize, and optionally upload telemetry")
    add_date_arguments(sync_parser)
    sync_parser.add_argument("--json", default="data/telemetry.local.json")
    sync_parser.add_argument("--publish", action="store_true")
    sync_parser.add_argument("--ingest-url", default=os.environ.get("TELEMETRY_INGEST_URL", ""))
    sync_parser.add_argument("--ingest-token", default=os.environ.get("TELEMETRY_INGEST_TOKEN", ""))
    return root


def main() -> int:
    """Dispatch one CLI command and keep operational failures user-readable.

    Upload settings are required only for publishing, and failures are reported
    without printing secret values.
    """
    load_local_dotenv()
    args = parser().parse_args()
    try:
        if args.command == "pull":
            pull(args)
        elif args.command == "normalize":
            payload = normalize_csv(Path(args.csv))
            write_json(Path(args.json), payload)
            print(f"Wrote {len(payload['events'])} normalized telemetry events to {args.json}")
        elif args.command == "publish":
            if not args.ingest_url or not args.ingest_token:
                raise RuntimeError("Set TELEMETRY_INGEST_URL and TELEMETRY_INGEST_TOKEN, or pass both arguments.")
            payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
            result = publish(payload, args.ingest_url, args.ingest_token)
            print(f"Published {result.get('events', 0)} telemetry events at {result.get('generatedAt', 'unknown time')}")
        else:
            csv_path = pull(args)
            payload = normalize_csv(csv_path)
            write_json(Path(args.json), payload)
            print(f"Wrote {len(payload['events'])} normalized telemetry events to {args.json}")
            if args.publish:
                if not args.ingest_url or not args.ingest_token:
                    raise RuntimeError("Set TELEMETRY_INGEST_URL and TELEMETRY_INGEST_TOKEN, or pass both arguments.")
                result = publish(payload, args.ingest_url, args.ingest_token)
                print(f"Published {result.get('events', 0)} telemetry events at {result.get('generatedAt', 'unknown time')}")
    except (RuntimeError, OSError, requests.RequestException, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
