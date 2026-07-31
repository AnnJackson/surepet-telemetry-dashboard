#!/usr/bin/env python3
"""Create a publishable telemetry demo from a private Sure Pet export.

The script retains the event timing, signed food-change magnitude, event type,
and source ``Context`` value for named pets. It deliberately replaces private
IDs, device IDs, device names, and API endpoints with stable public labels.

It also adds a small, clearly synthetic set of water examples. A household may
not have water events in the selected time range, but these examples make the
portable data contract easier to understand without pretending they are pet
consumption.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from surepet_telemetry import attribution_for_context, normalize_csv, write_csv, write_json


def parse_timestamp(value: str) -> datetime | None:
    """Return a UTC timestamp, or ``None`` for an incomplete source row."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def slug(value: str) -> str:
    """Make a stable public identifier without carrying a source-system ID."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


def context_value(row: dict[str, str]) -> str:
    """Keep the raw context concept while making integer-like values readable."""
    value = str(row.get("Context", "")).strip()
    return value[:-2] if value.endswith(".0") else value


def number(value: str) -> float | None:
    """Read a signed source measurement without inventing a value for blanks."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def public_event(row: dict[str, str], pets: set[str]) -> dict[str, Any] | None:
    """Convert one export row into the project's source-oriented CSV shape.

    Food and non-notification water changes use their absolute magnitude. This
    reflects the physical size of Sure Pet's signed device delta; no outlier
    cap, replacement value, or consumption estimate is applied here.
    """
    name = row.get("Pet Name", "").strip()
    kind = row.get("Type", "").strip()
    occurred_at = row.get("Timestamp", "").strip()
    if name not in pets or not occurred_at:
        return None
    event: dict[str, Any] = {
        "recordedAt": occurred_at,
        "subjectId": f"cat-{slug(name)}",
        "subjectName": name,
        "category": "",
        "action": "",
        "quantity": "",
        "unit": "",
        "occurredAt": occurred_at,
        "durationSeconds": row.get("Duration", ""),
        "deviceId": "",
        "deviceName": "",
        "context": context_value(row),
        "sourceEndpoint": "/demo",
        "attribution": attribution_for_context(context_value(row)),
    }
    amount = number(row.get("Amount", ""))
    if kind == "Food" and amount is not None:
        event.update({
            "category": "feeding", "action": "food_change",
            "quantity": abs(amount), "unit": "g",
            "deviceId": f"feeder-{slug(name)}", "deviceName": f"{name}'s feeder",
        })
    elif kind == "Water" and amount is not None:
        event.update({
            "category": "hydration", "action": "fountain_change",
            "quantity": abs(amount), "unit": "g",
            "deviceId": "fountain", "deviceName": "Fountain",
        })
    elif kind == "Movement":
        event.update({
            "category": "access", "action": "passage",
            "deviceId": "pet-flap", "deviceName": "Pet flap",
        })
    else:
        return None
    return event


def supplemental_events(end: datetime) -> list[dict[str, Any]]:
    """Add synthetic water examples that remain distinct from food consumption."""
    day = end.date().isoformat()
    return [
        {"recordedAt": f"{day}T07:04:00Z", "subjectId": "cat-pascal", "subjectName": "Pascal", "category": "hydration", "action": "fountain_change", "quantity": 9, "unit": "g", "occurredAt": f"{day}T07:04:00Z", "durationSeconds": 15, "deviceId": "fountain-kitchen", "deviceName": "Kitchen fountain", "context": "1", "sourceEndpoint": "/demo-supplemental", "attribution": "pet"},
        {"recordedAt": f"{day}T17:40:00Z", "subjectId": "cat-joule", "subjectName": "Joule", "category": "hydration", "action": "fountain_change", "quantity": 7, "unit": "g", "occurredAt": f"{day}T17:40:00Z", "durationSeconds": 14, "deviceId": "fountain-back", "deviceName": "Back fountain", "context": "1", "sourceEndpoint": "/demo-supplemental", "attribution": "pet"},
        {"recordedAt": f"{day}T18:58:00Z", "subjectId": "", "subjectName": "", "category": "ambient", "action": "water_removed", "quantity": 18, "unit": "ml", "occurredAt": f"{day}T18:58:00Z", "durationSeconds": "", "deviceId": "fountain-kitchen", "deviceName": "Kitchen fountain", "context": "", "sourceEndpoint": "/demo-supplemental", "attribution": "ambient"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sanitize a private Sure Pet export into public demo data")
    parser.add_argument("--input", required=True, help="Private surepet_events.csv export")
    parser.add_argument("--end", required=True, help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument("--weeks", type=int, default=6, help="Number of complete weeks to retain")
    parser.add_argument("--pets", default="Pascal,Joule", help="Comma-separated pet names to include")
    parser.add_argument("--csv", default="data/demo_events.csv")
    parser.add_argument("--json", default="data/demo_telemetry.json")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    start = end - timedelta(days=args.weeks * 7 - 1)
    exclusive_end = end + timedelta(days=1)
    pets = {pet.strip() for pet in args.pets.split(",") if pet.strip()}
    with Path(args.input).open(encoding="utf-8-sig", newline="") as source:
        raw_rows = csv.DictReader(source)
        events = []
        for row in raw_rows:
            timestamp = parse_timestamp(row.get("Timestamp", ""))
            if timestamp is None or not start <= timestamp < exclusive_end:
                continue
            event = public_event(row, pets)
            if event:
                events.append(event)
    events.extend(supplemental_events(end))
    events.sort(key=lambda event: event["occurredAt"])
    write_csv(Path(args.csv), events)
    payload = normalize_csv(Path(args.csv))
    payload["generatedAt"] = f"{args.end}T23:59:59Z"
    write_json(Path(args.json), payload)
    print(f"Wrote {len(events)} sanitized demo events from {start.date()} through {args.end}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
