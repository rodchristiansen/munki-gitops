#!/usr/bin/env python3
"""Gate the pipeline on these. No tenant, no credentials, no dependencies
beyond PyYAML.

The assertions are the failures that are invisible in production: a condition
that silently would not have been applied, an assignment set that has collapsed,
and an exclusion that looks meaningful and subtracts nothing.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from lib.conditions import CondParseError, UnsupportedCondition, translate  # noqa: E402
from lib.guards import GuardTripped, assignment_floor  # noqa: E402
from lib.manifests import load_tree, walk_managed_key  # noqa: E402
from stages.lint_conditions import lint_tree  # noqa: E402
from stages.plan_assignments import build_plan, resolve_exclusions  # noqa: E402

MANIFESTS = HERE.parent / "manifests"
BROKEN = HERE / "fixtures" / "broken"


class Translation(unittest.TestCase):
    def test_supported_predicates(self):
        for cond, fragment in [
            ("os_vers_major >= 15", '-ge "15"'),
            ('machine_type == "laptop"', '-startsWith "MacBook"'),
            ('hostname BEGINSWITH "LAB"', '-startsWith "LAB"'),
            ('hostname CONTAINS "STUDIO"', '-contains "STUDIO"'),
        ]:
            with self.subTest(cond=cond):
                _display, rule = translate(cond)
                self.assertIn(fragment, rule)

    def test_untranslatable_facts_raise_unsupported(self):
        for cond in ['arch == "arm64"', 'catalogs CONTAINS "Testing"',
                     'serial_number == "SAMPLEMAC001"']:
            with self.subTest(cond=cond):
                with self.assertRaises(UnsupportedCondition):
                    translate(cond)

    def test_machine_model_looks_supported_and_is_not(self):
        """filter `model` is the marketing name, never the hardware id."""
        with self.assertRaises(UnsupportedCondition):
            translate('machine_model == "MacBookPro16,1"')

    def test_malformed_conditions_raise_parse_error(self):
        for cond in ["os_vers_major >=", "(os_vers_major >= 15", ""]:
            with self.subTest(cond=cond):
                with self.assertRaises(CondParseError):
                    translate(cond)

    def test_unsupported_and_malformed_are_different_failures(self):
        """One is a limit of the platform, the other is a bug in the manifest.
        A caller that treats them the same cannot report either usefully."""
        self.assertFalse(issubclass(UnsupportedCondition, CondParseError))
        self.assertFalse(issubclass(CondParseError, UnsupportedCondition))


class ManifestWalk(unittest.TestCase):
    def test_conditions_combine_with_and(self):
        data = {
            "conditional_items": [{
                "condition": 'machine_type == "laptop"',
                "conditional_items": [{
                    "condition": "os_vers_major >= 15",
                    "managed_profiles": ["Nested"],
                }],
            }]
        }
        found = list(walk_managed_key(data, "managed_profiles"))
        self.assertEqual(len(found), 1)
        name, cond = found[0]
        self.assertEqual(name, "Nested")
        self.assertIn("AND", cond)

    def test_manifests_outside_usage_roots_are_not_addresses(self):
        groups = {group for _p, group, _d in load_tree(MANIFESTS)}
        self.assertIn("Devices-Assigned-Staff-IT", groups)
        self.assertNotIn(None, groups)


class Linting(unittest.TestCase):
    def test_sample_tree_is_clean(self):
        self.assertEqual(lint_tree(MANIFESTS), [])

    def test_broken_tree_reports_every_rule(self):
        findings = lint_tree(BROKEN)
        problems = " ".join(f.problem for f in findings)
        self.assertIn("arch", problems)
        self.assertIn("marketing name", problems)
        self.assertIn("Malformed", problems)
        self.assertIn("never read", problems)
        self.assertIn("rejects", problems)
        self.assertGreaterEqual(len(findings), 5)

    def test_untranslatable_condition_is_never_silent(self):
        """The failure this stage exists for: a skip with no finding."""
        findings = lint_tree(BROKEN)
        self.assertTrue(any("arch" in f.problem for f in findings),
                        "an untranslatable condition produced no finding")


class Planning(unittest.TestCase):
    def setUp(self):
        os.environ["MIN_DESIRED_ASSIGNMENTS"] = "3"

    def test_plan_resolves_paths_to_groups(self):
        assignments, _excl, skipped = build_plan(MANIFESTS)
        self.assertEqual(skipped, [])
        pairs = {(a.key, a.name, a.group) for a in assignments}
        self.assertIn(("managed_profiles", "AdminTerminalPrefs",
                       "Devices-Assigned-Staff-IT"), pairs)
        self.assertIn(("managed_apps", "Remote Desktop",
                       "Devices-Assigned-Staff-IT"), pairs)

    def test_conditional_profile_carries_a_filter(self):
        assignments, _e, _s = build_plan(MANIFESTS)
        modern = [a for a in assignments if a.name == "ModernOSPrefs"]
        self.assertTrue(modern)
        self.assertIn("osVersion", modern[0].filter_rule)

    def test_exclusion_is_live_only_when_inherited(self):
        assignments, exclusions, _s = build_plan(MANIFESTS)
        live, inert = resolve_exclusions(assignments, exclusions)
        self.assertIn(("SharedSoftwareUpdate", "Devices-Shared-Curriculum-Design"), live)
        self.assertTrue(any(name == "OldSecurityAgent" for name, _g in inert))

    def test_conditional_managed_apps_is_reported_not_pretended(self):
        _a, _e, skipped = build_plan(BROKEN)
        self.assertTrue(any("never read" in why for _n, _c, why in skipped))

    def test_collapsed_walk_trips_the_floor(self):
        os.environ["MIN_DESIRED_ASSIGNMENTS"] = "500"
        with self.assertRaises(GuardTripped):
            assignment_floor(len(build_plan(MANIFESTS)[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
