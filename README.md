# Return Relay

One late camera return can collide with the next pickup. Moving that booking to a free-looking body may collide with a second booking. Return Relay searches the supplied fleet snapshot for a complete compatible reassignment with the fewest changed bookings.

The included scenario moves PICKUP-1 from CAM-A to CAM-B and PICKUP-2 from CAM-B to CAM-C. PICKUP-3 keeps its original asset and pickup time. The output includes the exact moves, original conflict witnesses and a before/after timeline.

## Private development draft

This is a development prototype, not a released booking system. All included inputs were invented from scratch. No customer, employer or business records are included.

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

## Turnaround and checked-out equipment

Use version 2 snapshots, as in `examples/turnaround.json`. Every booking must explicitly provide `turnaround_minutes` (0–10080) and `locked` (true or false). A locked booking can only keep its assigned asset. Set this for equipment already checked out or assignments that cannot change. Turnaround extends occupancy after the return time, including conflicts with downtime. Unavailability intervals must include any recovery/inspection time after the unavailable equipment returns. Version 1 remains supported with zero turnaround and all bookings movable; do not use it when those assumptions are false.

In the turnaround example, 30 minutes of cleaning prevents PICKUP-2 from using CAM-C immediately before PICKUP-3. The supplied compatibility choices allow PICKUP-3 to move to CAM-A, producing a three-swap plan. If that substitution is unacceptable, remove CAM-A from its accepted list and the tool reports no complete plan.

```sh
python3 relay.py examples/turnaround.json --out proposal
python3 relay.py examples/turnaround.json --accept proposal/plan.json --out accepted
```

Review the proposal and physical readiness before running the second command. Acceptance checks the exact input fingerprint and recomputes the complete proposal. Changed input or an edited plan is refused. The new directory contains `accepted-snapshot.json`, `dispatch.csv`, an acceptance receipt and the comparison. Keep the original input and use the accepted snapshot as the next planning input. These files record your decision; your booking system still needs updating. No external booking is changed by this command.

The current interface requires manual JSON entry. Operator usability, timeline readability at small sizes and independent release review are still pending. Large-fleet scheduling, automatic substitution judgments and live inventory synchronization are outside this draft's scope.


## Build a complete disruption window

Start from your authoritative booking calendar. List every compatible asset you may use, every overlapping reservation on those assets, all locked/check-out assignments, and their unavailability. Include bookings whose turnaround extends into the window and later pickups reached by those turnaround tails. Repeat that overlap check until no included booking or buffer reaches an omitted reservation. Do not select only the initially affected booking: the second and third pickups in the example are necessary to discover the collision. A reservation omitted from the snapshot cannot constrain this solver.

Keep the booking system as the source of truth. Before accepting, compare that system with the snapshot again: asset readiness, new bookings, changed times, substitution permissions and locks. If anything changed, update the snapshot and generate/review a new proposal. The fingerprint catches edits to the supplied snapshot; it cannot detect an external calendar change that you have not entered.

After acceptance, transfer every change in `acceptance.json` to the authoritative calendar, using `dispatch.csv` to check each booking ID, asset, pickup and ready-again time. Re-read the calendar and mark your own reconciliation complete only after all changes match. Acceptance is a file export, not a transaction in the booking system. If an external update fails midway, preserve the exported plan, take a fresh snapshot of actual assignments, and replan; do not assume a partly applied chain is safe. Avoid concurrent booking edits during this handoff. Use a managed rental platform when manual reconciliation is unsuitable.
