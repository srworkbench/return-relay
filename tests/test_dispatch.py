import unittest
import xml.etree.ElementTree as ET
from relay import plan, render_dispatch
from test_constraints import upgraded


class DispatchReport(unittest.TestCase):
    def test_failure_cannot_look_successful(self):
        data = upgraded()
        data['bookings'][0]['locked'] = True
        for result in [plan(data), plan(upgraded(), node_limit=1)]:
            svg = render_dispatch(data, result)
            ET.fromstring(svg)
            self.assertIn('No accepted dispatch plan', svg)
            self.assertNotIn('Pickup times preserved', svg)
            self.assertNotIn('0 asset swaps', svg)

    def test_actual_counterexample_is_shown(self):
        data = upgraded()
        data['bookings'][1]['turnaround_minutes'] = 30
        data['bookings'][2]['accepts'].append('CAM-A')
        svg = render_dispatch(data, plan(data))
        ET.fromstring(svg)
        for phrase in ['3 asset swaps', '2 swaps look sufficient', 'ready 16:30 vs pickup 16:00', 'Return 16:00 + 30 min turnaround']:
            self.assertIn(phrase, svg)

    def test_no_counterexample_is_invented(self):
        data = upgraded()
        self.assertNotIn('Ignoring turnaround', render_dispatch(data, plan(data)))
