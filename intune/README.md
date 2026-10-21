# intune

The manifest tree becomes MDM state. Three keys the client ignores —
`managed_apps`, `managed_profiles`, `managed_scripts` — ride along in the same
reviewed YAML, and this layer renders them into Intune: VPP apps, mobileconfig
and Settings Catalog profiles, and shell scripts.

The engine that does this is shared with the Windows sibling and lives in
[windowsadmins/intune-gitops](https://github.com/windowsadmins/intune-gitops).
This directory keeps only what is Munki's: the manifests, the profiles, the
catalog and PayloadVersion check and the pipelines that call the engine at a
pinned tag (currently `v0.1.5`). Stages, guards, the condition translator and
their tests are documented and tested there.

The Windows half of the same pattern lives in
[cimian-gitops](https://github.com/windowsadmins/cimian-gitops). Same keys, same
path-to-group rule, same engine; only the render targets differ.

**This writes to a live tenant.** Every stage plans before it writes, and
whatIf is the default. Run the plan first, read the object names it prints, and
only then let it write.

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

## Layout

| Path | What it is |
|---|---|
| `manifests/` | The sample manifest tree. A path is an address: `manifests/Assigned/Staff/IT.yaml` is the group `Devices-Assigned-Staff-IT`. |
| `profiles/` | Profile definitions the manifests name, including `profiles/Apps/` mobileconfigs. |
| `checks/profile_invariants.py` | The Munki-only check: every profile and managed app declares a cumulative prefix of Development, Testing, Staging, Production, and every payload's `PayloadVersion` is the integer 1. |
| `pipelines/azure/intune-mgmt.yml` | Azure Pipelines caller for the intune-gitops stages template. |
| `pipelines/github/intune-mgmt.yml` | GitHub Actions caller for the intune-gitops composite action. Copy it into `.github/workflows/` to run it. |
| `pipelines/reference/` | The production pipeline, sanitized. See below. |

## Try it, offline

Check the engine out beside this repo at the pinned tag, and install PyYAML:

```
git clone --branch v0.1.5 https://github.com/windowsadmins/intune-gitops ../intune-gitops
```

```
pip install pyyaml
```

From the repo root, lint the tree. Every condition has to translate into an
assignment filter, or the build fails:

```
python3 ../intune-gitops/engine/stages/lint_conditions.py --platform macos intune/manifests
```

Then plan it. The plan is the artefact worth reading in a pull request. On the
sample tree it resolves sixteen assignments across three keys, attaches two
assignment filters, and reports one live exclusion and one inert one:

```
python3 ../intune-gitops/engine/stages/plan_assignments.py --platform macos intune/manifests
```

Then the Munki check:

```
python3 intune/checks/profile_invariants.py
```

## Conditions on macOS

Assignment filters expose only `deviceName`, `manufacturer`, `model`,
`osVersion`, `deviceCategory` and `enrollmentProfileName`. Munki conditions are
evaluated on the device against facts it collects locally, so they can
reference almost anything. Only `hostname`, `machine_type` and `os_vers_major`
translate, with `AND` / `OR` / `NOT` and parentheses.

Filters have no ordered comparison, so `os_vers_major >= 15` does not
translate; name the major version exactly instead:

```
- condition: os_vers_major == 26
```

`machine_model` looks supported and is not. The filter's `model` property holds
the marketing name (`MacBook Pro`), never the hardware identifier
(`MacBookPro16,1`), so a condition on the identifier would translate cleanly,
produce a valid filter, and match nothing. The linter refuses it.

The full table, and the rules Intune enforces on `NOT`, are in the
[intune-gitops README](https://github.com/windowsadmins/intune-gitops#conditions-become-assignment-filters).

## Pipelines

Both callers pin intune-gitops: the Azure one by `ref: refs/tags/v0.1.5` on its
repository resource, the GitHub one by the tag's commit SHA. An engine change
reaches this repo only when the pin moves. Both pass the Munki check into the
engine's test stage.

The real apply runs only from `main`, behind an environment, with a separate
write identity. The YAML conditions are a convenience; the boundary is the
approvals, branch control and required-template checks on the write service
connection and environment (Azure), or the environment protection rules and
federated credential (GitHub). Set those up from
[pipelines/README.md](https://github.com/windowsadmins/intune-gitops/blob/v0.1.5/pipelines/README.md)
in intune-gitops before the first real run.

`pipelines/reference/` is the production pipeline, lifted nearly as-is and
sanitized. It is there for accuracy, not as a model: several thousand lines of
script inline in YAML is why the engine moved into tested modules. Come here
when you want to see how it is really wired, or what a stage does that the
sample omits.

Sanitized means: service connection, variable group, storage account and
container names are placeholders, and the Teams webhook reads from a variable
group. If you lift your own, check that last one especially — a Power Automate
URL with a `sig=` parameter is a working credential, and a pipeline file is not
where it belongs.

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

## Graph permissions

The whatIf apply signs in with a read-only identity; the real apply with a
write identity used only inside the gated deployment.

- `DeviceManagementConfiguration.ReadWrite.All` — configuration profiles and Settings Catalog policies (write identity)
- `DeviceManagementManagedDevices.Read.All` — enumerate devices, force-sync
- `Group.Read.All` — resolve group names to ids

The enrollment layer needs more, because it creates groups and manages device
objects. See `../enrollment/README.md`.
