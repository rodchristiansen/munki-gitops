#!/usr/bin/env python3
"""Gate every membership sync on these.

The four assertions here are the failures that would be invisible in
production: a collapsed parse emptying groups, a runaway removal, a retired
device lingering, and a second run doing anything at all.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fake_graph import FakeGraph  # noqa: E402
from shared import guards  # noqa: E402
from shared.hierarchy import group_ladder, manifest_path, manifest_path_to_group  # noqa: E402
import consumers.intune as intune  # noqa: E402


ROWS = [
    {"serial": "SAMPLEMAC001", "usage": "Assigned", "catalog": "Staff",
     "area": "IT", "location": "B1101", "status": "Active"},
    {"serial": "SAMPLEMAC002", "usage": "Assigned", "catalog": "Staff",
     "area": "IT", "location": "B1101", "status": "Active"},
    {"serial": "SAMPLEMAC006", "usage": "Shared", "catalog": "Curriculum",
     "area": "Design", "location": "C2204", "status": "Active"},
    {"serial": "SAMPLEMAC013", "usage": "Shared", "catalog": "Curriculum",
     "area": "Design", "location": "C2210", "status": "Retired"},
]
OBJECTS = {r["serial"]: f"obj-{r['serial']}" for r in ROWS}


def run(rows, fake, **kw):
    """Drive the consumer against the fake, bypassing the real GraphClient."""
    real = intune.GraphClient
    intune.GraphClient = lambda **_: fake
    try:
        import csv, io
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        return intune.converge(buf.getvalue(), **kw)
    finally:
        intune.GraphClient = real


class LadderShape(unittest.TestCase):
    def test_five_rungs_broadest_to_narrowest(self):
        ladder = group_ladder(ROWS[0])
        self.assertEqual(ladder, [
            "Devices-All",
            "Devices-Assigned",
            "Devices-Assigned-Staff",
            "Devices-Assigned-Staff-IT",
            "Devices-Assigned-Staff-IT-B1101",
        ])

    def test_manifest_path_and_group_are_the_same_address(self):
        path = manifest_path(ROWS[0])
        self.assertEqual(path, "Assigned/Staff/IT.yaml")
        self.assertEqual(manifest_path_to_group("manifests/" + path),
                         "Devices-Assigned-Staff-IT")

    def test_stray_punctuation_does_not_fork_a_group(self):
        a = group_ladder({"usage": "Shared", "catalog": "Curriculum", "area": "Design Studio"})
        b = group_ladder({"usage": "Shared", "catalog": "Curriculum", "area": "DesignStudio"})
        self.assertEqual(a, b)

    def test_hostname_level_manifest_has_no_group_below_the_room(self):
        deep = "manifests/Assigned/Staff/IT/B1101/ALEXRIVERA.yaml"
        self.assertEqual(manifest_path_to_group(deep), "Devices-Assigned-Staff-IT-B1101")

    def test_manifest_outside_usage_roots_maps_nowhere(self):
        self.assertIsNone(manifest_path_to_group("manifests/CoreApps.yaml"))


class Convergence(unittest.TestCase):
    def setUp(self):
        os.environ["MIN_DESIRED_MEMBERSHIPS"] = "3"

    def test_active_devices_land_in_every_rung(self):
        fake = FakeGraph(devices=OBJECTS)
        self.assertEqual(run(ROWS, fake), 0)
        self.assertEqual(fake.members_of("Devices-Assigned-Staff-IT-B1101"),
                         {"obj-SAMPLEMAC001", "obj-SAMPLEMAC002"})
        self.assertEqual(len(fake.members_of("Devices-All")), 3)

    def test_retired_device_is_never_added(self):
        fake = FakeGraph(devices=OBJECTS)
        run(ROWS, fake)
        every = {oid for members in fake.members.values() for oid in members}
        self.assertNotIn("obj-SAMPLEMAC013", every)

    def test_second_run_changes_nothing(self):
        fake = FakeGraph(devices=OBJECTS)
        run(ROWS, fake)
        before_add, before_remove = len(fake.added), len(fake.removed)
        run(ROWS, fake)
        self.assertEqual((len(fake.added), len(fake.removed)),
                         (before_add, before_remove))

    def test_collapsed_parse_removes_nothing(self):
        fake = FakeGraph(devices=OBJECTS)
        run(ROWS, fake)
        populated = dict(fake.members)
        os.environ["MIN_DESIRED_MEMBERSHIPS"] = "500"
        with self.assertRaises(guards.GuardTripped):
            run(ROWS, fake)
        self.assertEqual(fake.members, populated)

    def test_runaway_removal_is_capped_and_reported(self):
        fake = FakeGraph(devices=OBJECTS)
        run(ROWS, fake)
        strangers = {f"obj-stranger-{i}" for i in range(40)}
        fake.preload("Devices-All", fake.members_of("Devices-All") | strangers)
        os.environ["MAX_REMOVAL_PCT"] = "10"
        self.assertEqual(run(ROWS, fake), 1)
        self.assertTrue(strangers <= fake.members_of("Devices-All"))

    def test_plans_offline_without_a_token(self):
        """Someone who has just forked the kit must be able to see the plan
        before handing it any credentials."""
        fake = FakeGraph(devices=OBJECTS, token="")
        self.assertEqual(run(ROWS, fake), 0)
        self.assertEqual(fake.created, [])
        self.assertEqual(fake.added, [])

    def test_what_if_writes_nothing(self):
        fake = FakeGraph(devices=OBJECTS, what_if=True)
        run(ROWS, fake, what_if=True)
        self.assertEqual(fake.added, [])
        self.assertEqual(fake.removed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
