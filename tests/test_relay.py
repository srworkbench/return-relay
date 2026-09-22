import copy
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from relay import InputError, conflicts, fingerprint, plan, read_snapshot, render, validate

ROOT = Path(__file__).resolve().parents[1]


def example():
    return json.loads((ROOT / 'examples/late-return.json').read_text())


class PlannerTests(unittest.TestCase):
    def test_chain_preserves_every_booking(self):
        data = example()
        before = copy.deepcopy(data)
        result = plan(data)
        self.assertEqual(result['status'], 'OPTIMAL')
        self.assertEqual(result['changed_count'], 2)
        self.assertEqual(result['proposal'], {'PICKUP-1': 'CAM-B', 'PICKUP-2': 'CAM-C', 'PICKUP-3': 'CAM-C'})
        self.assertEqual(conflicts(data, result['proposal']), [])
        self.assertEqual(data, before)

    def test_impossible_never_drops_booking(self):
        data = example()
        data['bookings'][1]['accepts'] = ['CAM-B']
        result = plan(data)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertIsNone(result['proposal'])

    def test_no_disruption_keeps_assignments(self):
        data = example()
        data['unavailable'] = []
        result = plan(data)
        self.assertEqual(result['changed_count'], 0)
        self.assertEqual(result['proposal'], result['before'])

    def test_budget_is_unknown_not_impossible(self):
        result = plan(example(), node_limit=1)
        self.assertEqual(result['status'], 'SEARCH_LIMIT')
        self.assertIsNone(result['proposal'])

    def test_boundary_is_half_open(self):
        data = example()
        data['unavailable'][0]['end'] = '2027-04-10T10:00Z'
        self.assertEqual(plan(data)['changed_count'], 0)
        data['unavailable'][0]['end'] = '2027-04-10T10:01Z'
        self.assertEqual(plan(data)['changed_count'], 2)

    def test_preexisting_collision_detected(self):
        data = example()
        data['bookings'][0]['assigned'] = 'CAM-B'
        result = plan(data)
        self.assertTrue(any(c['kind'] == 'booking_overlap' for c in result['before_conflicts']))
        self.assertEqual(conflicts(data, result['proposal']), [])

    def test_independent_exhaustive_oracle(self):
        # Independently compare raw integer intervals, not the production conflict checker.
        rng = random.Random(412)
        for _ in range(100):
            assets = ['A', 'B', 'C']
            raw = []
            for i in range(5):
                start = rng.randrange(0, 7)
                end = start + rng.randrange(1, 4)
                accepts = rng.sample(assets, rng.randrange(1, 4))
                raw.append((str(i), start, end, accepts, rng.choice(accepts)))
            blocked = rng.choice(assets)
            data = {'version': 1, 'assets': assets, 'bookings': [
                {'id': b, 'start': f'2027-04-10T{s:02}:00Z', 'end': f'2027-04-10T{e:02}:00Z', 'accepts': acc, 'assigned': orig}
                for b, s, e, acc, orig in raw],
                'unavailable': [{'asset': blocked, 'start': '2027-04-10T02:00Z', 'end': '2027-04-10T05:00Z'}]}
            costs = []
            for assignment in itertools.product(*(r[3] for r in raw)):
                if any(a == blocked and s < 5 and 2 < e for a, (_, s, e, _, _) in zip(assignment, raw)):
                    continue
                if any(assignment[i] == assignment[j] and raw[i][1] < raw[j][2] and raw[j][1] < raw[i][2]
                       for i in range(5) for j in range(i)):
                    continue
                costs.append(sum(a != r[4] for a, r in zip(assignment, raw)))
            result = plan(data)
            self.assertEqual(result['status'], 'OPTIMAL' if costs else 'INFEASIBLE')
            self.assertEqual(result['changed_count'], min(costs) if costs else None)

    def test_invalid_inputs(self):
        for path, value in [('start', '2027-02-30T10:00Z'), ('end', '2027-04-10T10:00Z'),
                            ('accepts', ['CAM-A', 'CAM-A']), ('assigned', 'UNKNOWN'), ('id', '<script>')]:
            data = example()
            data['bookings'][0][path] = value
            with self.assertRaises(InputError):
                validate(data)
        data = example()
        data['bookings'].append(copy.deepcopy(data['bookings'][0]))
        with self.assertRaises(InputError):
            validate(data)
        data = example()
        data['version'] = True
        with self.assertRaises(InputError):
            validate(data)

    def test_svg_is_actual_wellformed_output(self):
        data = example()
        result = plan(data)
        svg = render(data, result)
        ET.fromstring(svg)
        self.assertIn('2 reassigned bookings', svg)
        for b in data['bookings']:
            self.assertIn(b['id'], svg)

    def test_fingerprint_changes_with_input(self):
        data = example()
        old = fingerprint(data)
        data['unavailable'][0]['end'] = '2027-04-10T16:00Z'
        self.assertNotEqual(old, fingerprint(data))

    def test_cli_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'result'
            cmd = [sys.executable, str(ROOT / 'relay.py'), str(ROOT / 'examples/late-return.json'), '--out', str(out)]
            first = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            content = (out / 'plan.json').read_bytes()
            self.assertEqual(json.loads(content)['changed_count'], 2)
            second = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual((out / 'plan.json').read_bytes(), content)

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.json'
            path.write_text('{"version":1,"version":1}')
            with self.assertRaises(InputError):
                read_snapshot(path)


if __name__ == '__main__':
    unittest.main()
