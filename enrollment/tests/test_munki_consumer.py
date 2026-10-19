#!/usr/bin/env python3
"""The Munki consumer, run against the shared enrollment code in intune-gitops.

`shared/` and the `intune` consumer live in intune-gitops, so put both
enrollment directories on PYTHONPATH, this repo's first:

    PYTHONPATH="enrollment:../intune-gitops/enrollment" python3 -m unittest discover -s enrollment/tests -v
"""
from __future__ import annotations

import unittest
from unittest import mock

import consumers.munki as munki
from consumers import registry

HEADER = ",".join(munki.EXPECTED_HEADER)
ROW = "SAMPLEMAC001,Staff,IT,B1101,A001,Assigned,Active,Allocated,alex,Macintosh,STAFF-IT-001,"


class Publish(unittest.TestCase):
    def test_well_formed_projection_plans(self):
        self.assertEqual(munki.converge(f"{HEADER}\n{ROW}\n", what_if=True), 0)

    def test_wrong_header_is_refused(self):
        self.assertEqual(munki.converge(f"serial,catalog\n{ROW}\n", what_if=True), 1)

    def test_empty_projection_is_refused(self):
        self.assertEqual(munki.converge(f"{HEADER}\n", what_if=True), 1)

    def test_floor_is_enforced(self):
        with mock.patch.object(munki, "MIN_ROWS", 5):
            self.assertEqual(munki.converge(f"{HEADER}\n{ROW}\n", what_if=True), 1)


class Projection(unittest.TestCase):
    def test_inventory_projection_matches_the_published_header(self):
        import csv
        import pathlib
        import subprocess
        import sys
        import tempfile

        root = pathlib.Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([sys.executable, str(root / "inventory/projections/project.py"),
                            str(root / "inventory/inventory.csv"), "--out-dir", out],
                           check=True, capture_output=True)
            text = (pathlib.Path(out) / "munki.csv").read_text()
        self.assertEqual(next(csv.reader([text.splitlines()[0]])), munki.EXPECTED_HEADER)
        self.assertEqual(munki.converge(text, what_if=True), 0)


class Registry(unittest.TestCase):
    def test_munki_registers_beside_the_shared_intune_consumer(self):
        found = registry.load("munki=consumers.munki:converge")
        self.assertEqual(sorted(found), ["intune", "munki"])
        self.assertIs(found["munki"], munki.converge)


if __name__ == "__main__":
    unittest.main(verbosity=2)
