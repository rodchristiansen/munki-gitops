# intune

The manifest tree becomes MDM state. Three keys the client ignores —
`managed_apps`, `managed_profiles`, `managed_scripts` — ride along in the same
reviewed YAML, and this layer renders them into Intune.

**This writes to a live tenant.** Every stage plans before it writes, and
`WHATIF=true` is a supported way to run rather than a debug flag. Run the plan
first, read the actual object names it prints, and only then let it write.

## Why the addressing works

A manifest lives at `manifests/Assigned/Staff/IT.yaml`. That path is
`usage / catalog / area` — three of the twelve inventory columns. The same
columns built `Devices-Assigned-Staff-IT` in the enrollment layer, which
already exists and already contains the right machines.

```
manifests/Assigned/Staff/IT.yaml   ←→   Devices-Assigned-Staff-IT
```

A manifest path and a group name are the same address written twice, so this
layer never has to *decide* who anything applies to. Drop these keys into a
manifest tree that is not built this way and nothing happens, because there is
no group on the other end of the path. The keys are not the clever part; the
shared address space is.

## Try it, offline

```
python3 -m stages.lint_conditions manifests/
python3 -m stages.plan_assignments manifests/
```

The plan is the artefact worth reading in a pull request. On the sample tree it
resolves sixteen assignments across three keys, attaches two assignment filters,
and reports one live exclusion and one inert one.

Then the failure modes:

```
python3 -m stages.lint_conditions tests/fixtures/broken/
```

Five findings, exit 1.

## Stages

| Stage | Does |
|---|---|
| `lint_conditions` | Fails the build on anything the assign stages cannot honour. Runs first, gates everything. |
| `plan_assignments` | Resolves the tree into the assignments it implies. No tenant needed. |
| `apply_assignments` | Writes them. Refuses a partial plan, refuses an empty assignment set. |

## The four lint rules

The stage exists because of one specific failure. The assign stage, meeting a
condition it could not translate, logged one INFO line and skipped the block.
That is correct behaviour there — assigning without the filter would apply the
thing to every device in the group instead of the subset you meant. But a skip
is invisible. A phased rollout aimed by an untranslatable condition simply did
not happen, on a green build.

1. `managed_profiles` / `managed_scripts` under a condition with no filter
   equivalent — the silent skip.
2. A malformed condition.
3. `managed_apps` nested under `conditional_items` — the app stage reads only
   top-level `managed_apps`, so a conditional one is dropped without a word.
4. A condition that translates but emits an operator the platform rejects.

## What a condition can and cannot say

Assignment filters expose only `deviceName`, `manufacturer`, `model`,
`osVersion`, `deviceCategory` and `enrollmentProfileName`. Munki conditions are
evaluated on the device against facts it collects locally, so they can reference
almost anything. Everything hard about `lib/conditions.py` comes from that gap.

Supported: `hostname`, `machine_type`, `os_vers_major`, with `AND` / `OR` /
`NOT` and parentheses.

Not supported: `arch`, `serial_number`, `catalogs`, custom facts — and
`machine_model`, which looks supported and is not. The filter's `model`
property holds the marketing name (`MacBook Pro`), never the hardware
identifier (`MacBookPro16,1`), so a condition on the identifier translates
cleanly, produces a valid filter, and matches nothing.

`lib/conditions.py` is imported by both the linter and the assign stages. In
the production pipeline this logic exists twice with a comment between the
copies saying "keep these in sync", which is a module wearing a disguise. This
is the module.

## Guards

**Assignment is a full replace.** You do not add a group to a policy; you send
the complete list of who it applies to. An empty list is a successful call that
unassigns the policy from every device — and it is exactly what the code
produces when a manifest walk returns nothing. No exception, no error, a green
build, and a fleet quietly falling out of policy.

`MIN_DESIRED_ASSIGNMENTS` is the floor that catches it. Set it from your own
baseline and record that baseline in a comment with a date.

There is deliberately **no clear-ratio guard** — no check on how much of what
already exists in the tenant a run would clear. Measured against a live tenant,
a healthy run clears roughly a third of existing macOS configs, because a
tenant holds many policies no manifest assigns. "Existing minus desired" is
dominated by those, so a ratio on it fires constantly on healthy data while
saying nothing about a collapse. Worse, it trained people to re-run with the
override on, and a guard people routinely bypass costs the same attention and
buys nothing.

**Ownership markers.** Only objects whose description carries
`INTUNE_MANAGED_MARKER` are ever modified. Everything else in the tenant belongs
to somebody else. Put the marker in on day one — retrofitting it means going
object by object deciding what you are allowed to touch, which is the audit the
pipeline was supposed to remove.

**Exclusions that subtract nothing are not sent.** An exclusion is meaningful
only when its group is a strict descendant of a group the item actually reaches.
An exclusion list full of no-ops is a list nobody reads, which is how the one
that matters gets missed.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `WHATIF` | unset | `true` to log every write instead of making it |
| `MIN_DESIRED_ASSIGNMENTS` | `3` | Floor below which a run is treated as a parse failure |
| `INTUNE_MANAGED_MARKER` | `managed-by-gitops` | Marks the objects this pipeline owns |
| `INTUNE_GROUP_PREFIX` | `Devices` | First component of every group name |
| `PROTECTED_PROFILES` | `protected-profiles.yaml` | Profiles another pipeline owns |
| `GRAPH_TOKEN` | unset | Required only to apply |

## Graph permissions

- `DeviceManagementConfiguration.ReadWrite.All` — configuration profiles and Settings Catalog policies
- `DeviceManagementManagedDevices.Read.All` — enumerate devices, force-sync
- `Group.Read.All` — resolve group names to ids

The enrollment layer needs more, because it creates groups and manages device
objects. See `../enrollment/README.md`.

## Tests

```
python3 tests/test_intune.py
```

Fifteen assertions covering translation, the manifest walk, all four lint rules,
filter attachment, exclusion liveness, and the assignment floor. No tenant, no
credentials, PyYAML the only dependency.
