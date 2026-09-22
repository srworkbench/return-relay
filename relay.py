"""Small-fleet reassignment planner. Python 3.9+, standard library only."""
import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
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


def overlap(left, right):
    return left[0] < right[1] and right[0] < left[1]


def validate(data):
    fields(data, ['version', 'assets', 'bookings', 'unavailable'], 'snapshot')
    if type(data['version']) is not int or data['version'] != 1:
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
        fields(row, ['id', 'start', 'end', 'assigned', 'accepts'], 'booking')
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
            if asset == outage['asset'] and overlap(interval(row), interval(outage)):
                result.append({'booking': row['id'], 'asset': asset, 'kind': 'unavailable',
                               'start': outage['start'], 'end': outage['end']})
        for other in rows[:i]:
            if asset == assignment[other['id']] and overlap(interval(row), interval(other)):
                result.append({'booking': row['id'], 'other': other['id'], 'asset': asset,
                               'kind': 'booking_overlap'})
    return result


def plan(data, node_limit=200000):
    validate(data)
    if type(node_limit) is not int or node_limit < 1:
        raise InputError('Node limit must be a positive integer')
    rows = data['bookings']
    windows = {r['id']: interval(r) for r in rows}
    original = {r['id']: r['assigned'] for r in rows}
    allowed = {
        r['id']: sorted((a for a in r['accepts'] if not any(
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
    return {'version': 1, 'input_sha256': fingerprint(data), 'status': status,
            'nodes': nodes, 'node_limit': node_limit, 'before': original,
            'before_conflicts': conflicts(data, original), 'proposal': best,
            'changed_count': best_cost if best is not None else None,
            'changes': [{'booking': b, 'from': original[b], 'to': best[b]}
                        for b in sorted(original) if best is not None and best[b] != original[b]],
            'next_action': {'OPTIMAL': 'Review asset readiness and apply the proposal to your booking record.',
                            'INFEASIBLE': 'No complete assignment fits these constraints. Arrange outside stock or agree a booking change; rerun.',
                            'SEARCH_LIMIT': 'Search budget reached. Narrow the disruption window; no minimum or infeasibility claim.'}[status]}


def render(data, result):
    """Actual allocation output, with a shared time axis for before and proposal."""
    rows = sorted(data['bookings'], key=lambda r: (r['start'], r['id']))
    stamps = [t for r in rows + data['unavailable'] for t in interval(r)]
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
                text(x(a,origin) + 4, yy + 20, row['id'], 15, '#101b2d')
                lane += 1
    text(32, height-36, 'Red zone: unavailable  ·  Mint: reassigned  ·  Fixed booking times', 18, '#aebed5')
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


def main():
    parser = argparse.ArgumentParser(description='Propose compatible rental-asset swaps after a disruption')
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--out', required=True, type=Path, help='New output directory; never overwritten')
    args = parser.parse_args()
    temp = None
    try:
        data = read_snapshot(args.snapshot)
        result = plan(data)
        # mkdir is the exclusive claim. Never overwrite a prior run, including a symlink.
        args.out.mkdir(parents=False, exist_ok=False)
        temp = args.out
        (temp / 'plan.json').write_text(json.dumps(result, indent=2) + '\n')
        (temp / 'comparison.svg').write_text(render(data, result))
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
