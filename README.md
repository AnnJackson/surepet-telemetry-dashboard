# Sure Pet Telemetry Dashboard

A local-first reference pipeline for Sure Pet telemetry: pull device reports,
write portable CSV, normalize them into categorized JSON, and optionally publish
that JSON to a dashboard endpoint.

This project deliberately separates event concepts:

- **feeding** — food changes associated with a pet
- **hydration** — fountain changes; not automatically a hydration claim
- **access** — pet-flap passage events
- **ambient** — observed changes that cannot be reliably attributed to a pet

The included `data/demo_events.csv` and `data/demo_telemetry.json` are a public
six-week Pascal/Joule sample ending July 30, 2026. It retains the real event
timing, food-change magnitudes, and Context values for those cats, while
replacing source IDs, device IDs, device names, and API endpoints with public
labels. The three water examples are explicitly synthetic because the selected
period contains no water telemetry.

## Local demo

```bash
python3 surepet_telemetry.py normalize --csv data/demo_events.csv --json /tmp/telemetry.json
python3 -m unittest discover -s tests
node --test tests/dashboard.test.mjs
```

The included mobile-first feeding dashboard reads `data/demo_telemetry.json`
by default. It has the same analytical layout as the working dashboard:
latest meal, cumulative daily pace, daily totals, feeding-time dots, rolling
detail, and weekly history. The sample covers six completed weeks for Pascal
and Joule, so every analytical view is immediately useful.

### Feeding interpretation

The raw and normalized data retain Sure Pet's numeric `context` value exactly.
The pull stage also adds an `attribution` label for the known codes below;
unrecognized values remain `unknown` rather than being guessed at.

| Context | Attribution |
| --- | --- |
| `1` | `pet` |
| `3` | `owner` |
| `5` | `food_addition` |
| `6` | `system_event` |

The feeding dashboard intentionally displays only `food_change` events whose
attribution is `pet`. Owner additions and system events remain available in
the data but do not count as meals. Sure Pet reports food changes as negative
weight deltas; the pull stage converts their magnitude to positive grams for
display. It does **not** cap, replace, or otherwise alter a large reading.
Consumers of the data can inspect the raw event and decide how to handle an
outlier for their own household.

From the repository root, start a local web server and open the dashboard in a
browser:

```bash
python3 -m http.server 8000
```

Then visit [http://localhost:8000/dashboard/](http://localhost:8000/dashboard/).
It intentionally presents food changes only for now. The normalized data model
still retains fountain, access, and ambient categories for a future dashboard
extension. You can point the dashboard at another schema-v2 JSON file with a
`data` query parameter, for example:

```text
http://localhost:8000/dashboard/?data=../data/telemetry.local.json
```

## Live pull

Create a local `.env` from `.env.example`, then add your Sure Pet email and
password. The script loads this local file automatically; it is ignored by
Git. Settings already supplied by your operating system take precedence.

`SUREPET_DEVICE_ID` is a required, non-secret field in the login request; it
is **not** a physical feeder, fountain, or pet-flap ID and does not select
which devices are reported. This project uses the dummy client value
`0123456789`, matching a working Sure Pet integration.

```bash
python3 surepet_telemetry.py sync --start 2026-01-01
```

By default, `sync` writes `data/surepet_events.local.csv` and
`data/telemetry.local.json`. Both remain on the user’s computer and are
ignored by Git. Supply `--csv` or `--json` to use a different local location.
Add `--publish` only when you have configured a compatible endpoint. The
upload token is sent in `X-Telemetry-Token`; it is never stored by this
project.

## License

This project is available under the [MIT License](LICENSE), copyright Jackson
Two, LLC.

## Status

The pipeline, public demo data, and local browser dashboard are the initial
public-project foundation. Pet colors and the demo display time zone are
declared at the top of `dashboard/app.mjs` so a fork can adjust them without
changing the data pipeline.
