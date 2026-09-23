"""Small-fleet reassignment planner. Python 3.9+, standard library only."""
import argparse
import copy
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta
from html import escape
from pathlib import Path


class InputError(ValueError):
    pass


def fields(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise InputError(f'{label}: expected fields {", ".join(keys)}')


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value):
        raise InputError('IDs must contain 1–32 letters, digits, underscores or hyphens')
    return value


def minute(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z', value):
        raise InputError('Times must be UTC minutes: YYYY-MM-DDTHH:MMZ')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%MZ')
    except ValueError as exc:
        raise InputError(f'Invalid UTC time: {value}') from exc


def interval(row):
    start, end = minute(row['start']), minute(row['end'])
    if start >= end:
        raise InputError('Every interval needs start before end')
    return start, end


def occupied(row):
    start, end = interval(row)
    try:
        return start, end + timedelta(minutes=row.get("turnaround_minutes", 0))
    except OverflowError as exc:
        raise InputError("Turnaround extends beyond supported dates") from exc


def overlap(left, right):
    return left[0] < right[1] and right[0] < left[1]


def validate(data):
    fields(data, ['version', 'assets', 'bookings', 'unavailable'], 'snapshot')
    if type(data['version']) is not int or data['version'] not in (1, 2):
        raise InputError('Unsupported snapshot version')
    assets = data['assets']
    if not isinstance(assets, list) or not 1 <= len(assets) <= 8:
        raise InputError('Supply 1–8 unique assets')
    if len(set(map(ident, assets))) != len(assets):
        raise InputError('Duplicate asset ID')
    bookings = data['bookings']
    if not isinstance(bookings, list) or not 1 <= len(bookings) <= 14:
        raise InputError('Supply 1–14 bookings; this planner is for small disruption windows')
    seen = set()
    for row in bookings:
        fields(row, ['id', 'start', 'end', 'assigned', 'accepts'] +
               (['turnaround_minutes', 'locked'] if data['version'] == 2 else []), 'booking')
        if data['version'] == 2:
            if type(row['locked']) is not bool:
                raise InputError('locked must be true or false')
            if type(row['turnaround_minutes']) is not int or not 0 <= row['turnaround_minutes'] <= 10080:
                raise InputError('turnaround_minutes must be an integer from 0 to 10080')
        occupied(row)
        if ident(row['id']) in seen:
            raise InputError('Duplicate booking ID')
        seen.add(row['id'])
        interval(row)
        accepts = row['accepts']
        if not isinstance(accepts, list) or not accepts:
            raise InputError('Each booking needs an explicit accepted asset list')
        if len(set(map(ident, accepts))) != len(accepts) or not set(accepts) <= set(assets):
            raise InputError('Accepted assets must be unique known IDs')
        if ident(row['assigned']) not in accepts:
            raise InputError('Original assignment must be accepted by its booking')
    if not isinstance(data['unavailable'], list) or len(data['unavailable']) > 100:
        raise InputError('Supply at most 100 unavailability intervals')
    for row in data['unavailable']:
        fields(row, ['asset', 'start', 'end'], 'unavailability')
        if ident(row['asset']) not in assets:
            raise InputError('Unknown unavailable asset')
        interval(row)
    return data


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def conflicts(data, assignment):
    """Return explicit witnesses, including pre-existing booking collisions."""
    result = []
    rows = data['bookings']
    for i, row in enumerate(rows):
        asset = assignment[row['id']]
        for outage in data['unavailable']:
            if asset == outage['asset'] and overlap(occupied(row), interval(outage)):
                result.append({'booking': row['id'], 'asset': asset, 'kind': 'unavailable',
                               'start': outage['start'], 'end': outage['end']})
        for other in rows[:i]:
            if asset == assignment[other['id']] and overlap(occupied(row), occupied(other)):
                result.append({'booking': row['id'], 'other': other['id'], 'asset': asset,
                               'kind': 'booking_overlap'})
    return result


def plan(data, node_limit=200000):
    validate(data)
    if type(node_limit) is not int or node_limit < 1:
        raise InputError('Node limit must be a positive integer')
    rows = data['bookings']
    windows = {r['id']: occupied(r) for r in rows}
    original = {r['id']: r['assigned'] for r in rows}
    allowed = {
        r['id']: sorted((a for a in ([r['assigned']] if r.get('locked', False) else r['accepts']) if not any(
            o['asset'] == a and overlap(windows[r['id']], interval(o))
            for o in data['unavailable'])), key=lambda a: (a != r['assigned'], a))
        for r in rows
    }
    best = None
    best_cost = len(rows) + 1
    nodes = 0
    exhausted = False

    def search(remaining, assigned, cost):
        nonlocal best, best_cost, nodes, exhausted
        if cost >= best_cost:
            return
        if nodes >= node_limit:
            exhausted = True
            return
        nodes += 1
        if not remaining:
            best, best_cost = dict(assigned), cost
            return
        options = {}
        for bid in remaining:
            options[bid] = [a for a in allowed[bid] if not any(
                a == placed and overlap(windows[bid], windows[other])
                for other, placed in assigned.items())]
        bid = min(remaining, key=lambda x: (len(options[x]), x))
        for asset in options[bid]:
            assigned[bid] = asset
            search(remaining - {bid}, assigned, cost + (asset != original[bid]))
            del assigned[bid]

    search(set(original), {}, 0)
    status = 'SEARCH_LIMIT' if exhausted else ('OPTIMAL' if best is not None else 'INFEASIBLE')
    # A budget-limited candidate is diagnostic, never an approved/minimal proposal.
    return {'version': 2, 'input_sha256': fingerprint(data), 'status': status,
            'nodes': nodes, 'node_limit': node_limit, 'before': original,
            'before_conflicts': conflicts(data, original), 'proposal': best,
            'locked_bookings': sorted(r['id'] for r in rows if r.get('locked', False)),
            'effective_end': {r['id']: occupied(r)[1].strftime('%Y-%m-%dT%H:%MZ') for r in rows},
            'changed_count': best_cost if best is not None else None,
            'changes': [{'booking': b, 'from': original[b], 'to': best[b]}
                        for b in sorted(original) if best is not None and best[b] != original[b]],
            'next_action': {'OPTIMAL': 'Review asset readiness and apply the proposal to your booking record.',
                            'INFEASIBLE': 'No complete assignment fits these constraints. Arrange outside stock or agree a booking change; rerun.',
                            'SEARCH_LIMIT': 'Search budget reached. Narrow the disruption window; no minimum or infeasibility claim.'}[status]}


def render(data, result):
    """Actual allocation output, with a shared time axis for before and proposal."""
    rows = sorted(data['bookings'], key=lambda r: (r['start'], r['id']))
    stamps = [t for r in rows for t in occupied(r)] + [t for r in data['unavailable'] for t in interval(r)]
    lo, hi = min(stamps), max(stamps)
    span = (hi - lo).total_seconds()
    height = 255 + len(data['assets']) * 104
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="{height}" viewBox="0 0 1080 {height}" role="img">',
           '<title>Return Relay allocation comparison</title>',
           '<rect width="100%" height="100%" fill="#101b2d"/>']

    def text(x, y, value, size=20, fill='#eff5ff'):
        out.append(f'<text x="{x}" y="{y}" fill="{fill}" font-family="Arial,sans-serif" font-size="{size}">{escape(str(value))}</text>')

    def x(time, origin):
        return origin + (time - lo).total_seconds() / span * 380

    text(32, 48, 'Return Relay', 32)
    text(32, 85, f'{result["status"]}  |  {len(result["before_conflicts"])} initial conflicts  |  '
         + (f'{result["changed_count"]} reassigned bookings' if result['proposal'] is not None else 'No complete plan'), 23)
    text(145, 128, 'BEFORE', 18, '#aebed5')
    text(635, 128, 'PROPOSED', 18, '#aebed5')
    for origin in (145, 635):
        text(origin, 156, lo.strftime('%m-%d %H:%M') + ' → ' + hi.strftime('%m-%d %H:%M UTC'), 16, '#aebed5')
    bad = {c['booking'] for c in result['before_conflicts']} | {c['other'] for c in result['before_conflicts'] if 'other' in c}
    changed = {c['booking'] for c in result['changes']}
    for i, asset in enumerate(data['assets']):
        y = 178 + i * 104
        text(32, y + 34, asset, 22)
        for origin, assignment in ((145, result['before']), (635, result['proposal'])):
            out.append(f'<rect x="{origin}" y="{y}" width="380" height="76" rx="6" fill="#1b2b43"/>')
            if assignment is None:
                continue
            for o in data['unavailable']:
                if o['asset'] == asset:
                    a, b = interval(o)
                    out.append(f'<rect x="{x(a,origin):.2f}" y="{y}" width="{x(b,origin)-x(a,origin):.2f}" height="76" fill="#663544" opacity="0.8"/>')
            lane = 0
            for row in rows:
                if assignment[row['id']] != asset:
                    continue
                a, b = interval(row)
                color = '#ffb09d' if origin == 145 and row['id'] in bad else ('#71dfc1' if row['id'] in changed else '#a5c3f4')
                yy = y + 8 + (lane % 2) * 33
                out.append(f'<rect x="{x(a,origin):.2f}" y="{yy}" width="{max(1,x(b,origin)-x(a,origin)):.2f}" height="28" rx="3" fill="{color}"/>')
                if row.get('turnaround_minutes', 0):
                    end = occupied(row)[1]
                    out.append(f'<rect x="{x(b,origin):.2f}" y="{yy}" width="{x(end,origin)-x(b,origin):.2f}" height="28" fill="#edc66f"/>')
                text(x(a,origin) + 4, yy + 20, row['id'] + (' *' if row.get('locked') else ''), 15, '#101b2d')
                lane += 1
    text(32, height-36, 'Red: unavailable  ·  Mint: reassigned  ·  Gold: turnaround  ·  * Locked', 18, '#aebed5')
    out.append('</svg>')
    return '\n'.join(out)


def read_snapshot(path):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise InputError(f'Duplicate JSON key: {k}')
            result[k] = v
        return result
    return validate(json.loads(path.read_text(), object_pairs_hook=unique))


def render_dispatch(data, result):
    """Readable decision cards generated from the exact proposed assignments."""
    rows = sorted(data['bookings'], key=lambda b: (b['start'], b['id']))
    settled = result['status'] == 'OPTIMAL'
    relaxed = copy.deepcopy(data)
    for row in relaxed['bookings']:
        if 'turnaround_minutes' in row:
            row['turnaround_minutes'] = 0
    naive = plan(relaxed)
    witnesses = conflicts(data, naive['proposal']) if naive['status'] == 'OPTIMAL' else []
    added = [w for w in witnesses if w not in conflicts(relaxed, naive['proposal'])] if witnesses else []
    show_counterexample = settled and bool(added)
    offset = 195 if show_counterexample else 0
    height = 220 + offset + 145 * len(rows)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" viewBox="0 0 900 {height}">',
             '<title>Return Relay proposed dispatch</title><rect width="100%" height="100%" fill="#102138"/>']

    def text(x, y, label, size=26, color='#eef5ff'):
        parts.append(f'<text x="{x}" y="{y}" font-family="Arial,sans-serif" font-size="{size}" fill="{color}">{escape(str(label))}</text>')

    text(36, 58, 'Return Relay · proposed dispatch', 36)
    headline = f'{result["changed_count"]} asset swaps · all bookings fit' if settled else 'No accepted dispatch plan'
    text(36, 107, headline, 32, '#75dfc0' if settled else '#ffc18f')
    text(36, 148, 'Pickup times preserved. Turnaround included.' if settled else result['status'] + ' · review constraints and replan', 26)
    if show_counterexample:
        w = added[0]
        parts.append('<rect x="26" y="168" width="848" height="184" rx="12" fill="#533142"/>')
        text(44, 207, f'Ignoring turnaround: {naive["changed_count"]} swaps look sufficient', 29, '#ffd6ba')
        if w['kind'] == 'booking_overlap':
            pair = sorted([r for r in rows if r['id'] in (w['booking'], w['other'])], key=lambda r: r['start'])
            first, second = pair
            text(44, 250, f'{w["asset"]}: ready {occupied(first)[1].strftime("%H:%M")} vs pickup {minute(second["start"]).strftime("%H:%M")}', 32)
            text(44, 291, f'Return {minute(first["end"]).strftime("%H:%M")} + {first.get("turnaround_minutes",0)} min turnaround', 27)
        else:
            text(44, 250, f'{w["asset"]}: turnaround overlaps unavailability', 28)
        text(44, 331, 'The proposal below includes the extra constraint.', 24, '#ffd6ba')
    for i, row in enumerate(rows):
        y = 175 + offset + i * 145
        parts.append(f'<rect x="26" y="{y}" width="848" height="129" rx="12" fill="#203752"/>')
        text(44, y + 34, row['id'] + (' · LOCKED' if row.get('locked') else ''), 26)
        target = result['proposal'][row['id']] if settled else 'UNRESOLVED'
        label = f'{row["assigned"]} → {target}'
        text(44, y + 74, label, min(30, 1200 / max(1, len(label))), '#75dfc0')
        text(44, y + 109, f'{row["start"]} · ready again {result["effective_end"][row["id"]]}', 21)
    text(36, height - 18, 'Review physical readiness before accepting the plan.', 23, '#bdcbe0')
    parts.append('</svg>')
    return '\n'.join(parts)


def accept(data, proposal):
    """Recompute instead of trusting editable proposal fields or just its input hash."""
    validate(data)
    if not isinstance(proposal, dict) or proposal.get('input_sha256') != fingerprint(data):
        raise InputError('Snapshot changed since planning; generate and review a new proposal')
    current = plan(data)
    if current['status'] != 'OPTIMAL':
        raise InputError('Only a proven complete optimal proposal can be accepted')
    if proposal != current:
        raise InputError('Proposal differs from the verified plan; generate and review it again')
    updated = copy.deepcopy(data)
    for booking in updated['bookings']:
        booking['assigned'] = current['proposal'][booking['id']]
    return updated


def main():
    parser = argparse.ArgumentParser(description='Propose compatible rental-asset swaps after a disruption')
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--out', required=True, type=Path, help='New output directory; never overwritten')
    parser.add_argument('--accept', type=Path, help='Reviewed plan.json to verify and export as an accepted snapshot')
    args = parser.parse_args()
    temp = None
    try:
        data = read_snapshot(args.snapshot)
        result = plan(data)
        updated = accept(data, json.loads(args.accept.read_text())) if args.accept else None
        # mkdir is the exclusive claim. Never overwrite a prior run, including a symlink.
        args.out.mkdir(parents=False, exist_ok=False)
        temp = args.out
        (temp / 'plan.json').write_text(json.dumps(result, indent=2) + '\n')
        (temp / 'comparison.svg').write_text(render(data, result))
        (temp / 'dispatch.svg').write_text(render_dispatch(data, result))
        if updated is not None:
            (temp / 'accepted-snapshot.json').write_text(json.dumps(updated, indent=2) + '\n')
            with (temp / 'dispatch.csv').open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['booking', 'asset', 'pickup_utc', 'return_utc', 'ready_again_utc', 'locked'])
                for booking in sorted(updated['bookings'], key=lambda b: (b['start'], b['id'])):
                    writer.writerow([booking['id'], booking['assigned'], booking['start'], booking['end'],
                                     result['effective_end'][booking['id']], booking.get('locked', False)])
            (temp / 'acceptance.json').write_text(json.dumps({
                'source_sha256': fingerprint(data), 'accepted_sha256': fingerprint(updated),
                'changes': result['changes'], 'external_calendar_updated': False}, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'changed_count': result['changed_count'],
                          'conflicts_before': len(result['before_conflicts']), 'out': str(args.out)}))
        temp = None
        return 0
    except (InputError, OSError, ValueError) as exc:
        if temp is not None:
            shutil.rmtree(temp)
        parser.exit(2, f'Cannot plan: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
