# Return Relay

One late camera return can collide with the next pickup. Moving that booking to a free-looking body may collide with a second booking. Return Relay searches the supplied fleet snapshot for a complete compatible reassignment with the fewest changed bookings.

The included scenario moves PICKUP-1 from CAM-A to CAM-B and PICKUP-2 from CAM-B to CAM-C. PICKUP-3 keeps its original asset and pickup time. The output includes the exact moves, original conflict witnesses and a before/after timeline.

## Private development baseline

This is an unfinished prototype, not a released booking system. All included inputs were invented from scratch. No customer, employer or business records are included.

Requires Python 3.9 or newer. No dependencies or account needed:

```sh
python3 relay.py examples/late-return.json --out output
python3 -m unittest discover -s tests -v
```

Open `output/comparison.svg` and inspect `output/plan.json`. Output directories must be new. Existing runs are never overwritten. Supply a different `--out` for each run.

## Entering a snapshot

Use the included JSON structure with your own asset IDs, bookings and unavailability intervals. Times must be explicit UTC minute strings. Intervals are half open: an interval ending at 16:00 does not overlap one starting at 16:00. Each booking lists the assets its operator accepts as substitutes. The tool never infers that two cameras or other pieces of equipment are interchangeable.

The baseline supports 1–8 assets, 1–14 bookings and up to 100 unavailability intervals. It searches at most 200,000 nodes. `OPTIMAL` means the fewest changed bookings among complete assignments under the supplied constraints. `INFEASIBLE` means no complete assignment exists. `SEARCH_LIMIT` means the search ended without proving either optimality or infeasibility; any included candidate is diagnostic only. No booking is silently dropped and times never move.

## Decision and tradeoff

The operator supplies a conservative unavailability horizon for an uncertain return and verifies physical readiness before changing any booking. A plan cannot make equipment return, establish customer consent or resolve a late-fee dispute. It does not sync calendars or reserve outside inventory. Existing rental platforms provide richer booking, pickup and return workflows. This prototype explores a portable, explicit swap proposal for a small disruption window, with the cost of manual snapshot entry.

Upcoming development must handle turnaround buffers and already checked-out bookings, then bind proposal acceptance to unchanged input and export the accepted schedule. Do not use the baseline where those constraints matter. A zero-buffer scenario is used only to exercise the initial search.
