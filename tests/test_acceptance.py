import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from relay import InputError, accept, plan
from test_constraints import upgraded
from test_relay import ROOT


class Acceptance(unittest.TestCase):
    def test_accept_preserves_input_and_reopens(self):
        data = upgraded()
        saved = copy.deepcopy(data)
        proposal = plan(data)
        updated = accept(data, proposal)
        self.assertEqual(data, saved)
        self.assertEqual(plan(updated)['changed_count'], 0)
        self.assertEqual([b['assigned'] for b in updated['bookings']], ['CAM-B', 'CAM-C', 'CAM-C'])

    def test_changed_constraints_invalidate_proposal(self):
        for field, value in [('turnaround_minutes', 30), ('locked', True), ('end', '2027-04-10T15:00Z')]:
            data = upgraded()
            proposal = plan(data)
            data['bookings'][0][field] = value
            with self.assertRaisesRegex(InputError, 'Snapshot changed'):
                accept(data, proposal)

    def test_tampered_plan_rejected_even_with_valid_fingerprint(self):
        data = upgraded()
        for field, value in [('proposal', {'PICKUP-1': 'CAM-A'}), ('changes', []), ('status', 'INFEASIBLE')]:
            proposal = plan(data)
            proposal[field] = value
            with self.assertRaises(InputError):
                accept(data, proposal)

    def test_infeasible_cannot_be_accepted(self):
        data = upgraded()
        data['bookings'][0]['locked'] = True
        with self.assertRaises(InputError):
            accept(data, plan(data))

    def test_cli_accept_and_stale_refusal_create_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            snapshot = base / 'snapshot.json'
            data = upgraded()
            snapshot.write_text(json.dumps(data))
            proposal = base / 'plan.json'
            proposal.write_text(json.dumps(plan(data)))
            out = base / 'accepted'
            cmd = [sys.executable, str(ROOT/'relay.py'), str(snapshot), '--accept', str(proposal), '--out', str(out)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((out/'dispatch.csv').exists())
            updated = json.loads((out/'accepted-snapshot.json').read_text())
            self.assertEqual(plan(updated)['changed_count'], 0)
            data['bookings'][0]['locked'] = True
            snapshot.write_text(json.dumps(data))
            cmd[-1] = str(base/'stale')
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertFalse((base/'stale').exists())
