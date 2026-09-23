import copy
import itertools
import random
import unittest

from relay import InputError, conflicts, plan, validate
from test_relay import example


def upgraded():
    data = example()
    data['version'] = 2
    for row in data['bookings']:
        row.update(turnaround_minutes=0, locked=False)
    return data


class Constraints(unittest.TestCase):
    def test_cleaning_breaks_zero_gap_chain(self):
        data = upgraded()
        self.assertEqual(plan(data)['changed_count'], 2)
        data['bookings'][1]['turnaround_minutes'] = 30
        self.assertEqual(plan(data)['status'], 'INFEASIBLE')
        data['bookings'][2]['accepts'].append('CAM-A')
        result = plan(data)
        self.assertEqual(result['status'], 'OPTIMAL')
        self.assertEqual(result['changed_count'], 3)
        self.assertEqual(result['proposal']['PICKUP-3'], 'CAM-A')
        self.assertEqual(conflicts(data, result['proposal']), [])

    def test_locked_booking_cannot_be_swapped_to_make_plan_fit(self):
        data = upgraded()
        data['bookings'][1]['locked'] = True
        result = plan(data)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertEqual(result['locked_bookings'], ['PICKUP-2'])

    def test_locked_original_outage_is_reported_not_moved(self):
        data = upgraded()
        data['bookings'][0]['locked'] = True
        self.assertEqual(plan(data)['status'], 'INFEASIBLE')

    def test_outage_during_cleaning_blocks_asset(self):
        data = upgraded()
        data['unavailable'] = [{'asset': 'CAM-A', 'start': '2027-04-10T14:15Z', 'end': '2027-04-10T14:20Z'}]
        self.assertEqual(plan(data)['changed_count'], 0)
        data['bookings'][0]['turnaround_minutes'] = 30
        self.assertEqual(plan(data)['changed_count'], 2)

    def test_invalid_constraints_and_overflow(self):
        for field, bad in [('locked', 'false'), ('locked', 0), ('turnaround_minutes', True),
                           ('turnaround_minutes', -1), ('turnaround_minutes', 10081)]:
            data = upgraded()
            data['bookings'][0][field] = bad
            with self.assertRaises(InputError):
                validate(data)
        data = upgraded()
        data['bookings'][0].update(start='9999-12-31T22:00Z', end='9999-12-31T23:59Z', turnaround_minutes=1)
        with self.assertRaises(InputError):
            validate(data)

    def test_exhaustive_oracle_with_interacting_constraints(self):
        rng = random.Random(811)
        for _ in range(150):
            raw = []
            for i in range(5):
                start = rng.randrange(7)
                end = start + rng.randrange(1, 3)
                accepts = rng.sample(['A', 'B', 'C'], rng.randrange(1, 4))
                raw.append(dict(id=str(i), start=start, end=end, buffer=rng.randrange(2),
                                accepts=accepts, assigned=rng.choice(accepts), locked=rng.choice([True, False])))
            data = {'version': 2, 'assets': ['A', 'B', 'C'], 'bookings': [],
                    'unavailable': [{'asset': 'A', 'start': '2027-04-10T03:00Z', 'end': '2027-04-10T05:00Z'}]}
            for r in raw:
                data['bookings'].append(dict(id=r['id'], start=f'2027-04-10T{r["start"]:02}:00Z',
                    end=f'2027-04-10T{r["end"]:02}:00Z', turnaround_minutes=60*r['buffer'],
                    locked=r['locked'], accepts=r['accepts'], assigned=r['assigned']))
            costs = []
            for values in itertools.product(*(r['accepts'] for r in raw)):
                if any(r['locked'] and a != r['assigned'] for r, a in zip(raw, values)):
                    continue
                if any(a == 'A' and r['start'] < 5 and 3 < r['end'] + r['buffer'] for r, a in zip(raw, values)):
                    continue
                if any(values[i] == values[j] and raw[i]['start'] < raw[j]['end'] + raw[j]['buffer']
                       and raw[j]['start'] < raw[i]['end'] + raw[i]['buffer'] for i in range(5) for j in range(i)):
                    continue
                costs.append(sum(a != r['assigned'] for r, a in zip(raw, values)))
            result = plan(data)
            self.assertEqual(result['status'], 'OPTIMAL' if costs else 'INFEASIBLE')
            self.assertEqual(result['changed_count'], min(costs) if costs else None)
