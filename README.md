# Running Munki under a GitOps model

**MacDevOps YVR 2025 presentation Companion Repo** - [YouTube link](https://www.youtube.com/watch?v=ayQqGT9S_cM&t=6s&pp=ygUQcm9kIGNocmlzdGlhbnNlbg%3D%3D)

Samples of running Munki entirely from Git: hooks, CI/CD pipelines, message
queues and local caching servers, with inventory driving Entra groups,
manifests and Intune assignments.

Each piece ships for **Azure** (Azure Pipelines or GitHub Actions, Blob
Storage, Front Door, Service Bus) and **AWS** (Azure Pipelines or GitHub
Actions, S3, CloudFront, SQS). Pick the cloud that fits your infrastructure.


## From manual to GitOps

The legacy flow was:

- GitLab running on-prem
- One shared Mac as the deploy point
- One central repo, updated by many hands
- No pipeline. No hooks. No approval gates.
- Everyone stepped on everyone’s toes

Now we have:

- Git repos and CI/CD pipelines (Azure Pipelines or GitHub Actions)
- Git hooks that upload/download packages automatically (Azure Storage or S3)
- Separate working copies per admin
- Local caching servers that sync intelligently
- A full CI/CD system that integrates with inventory and deploys via pull requests


## What is here

| Path | What it is |
|---|---|
| `githooks/` | Git hooks for an Azure Blob (`azure/`) or S3 (`aws/`) backed repo: pkgsinfo lint, package download on pull, create-only package upload on branch pushes, sync and capped orphan cleanup on main. See its README. |
| `pipelines/` | Push-to-production pipelines for Azure Pipelines (`azure/`) and GitHub Actions (`github/`), each against Azure Blob + Front Door or S3 + CloudFront. |
| `preflight/munki/` | A zsh preflight: picks a fresh local mirror or the cloud repo, looks the Mac up in inventory and sets its names and `ClientIdentifier`. |
| `local-caching/` | Commits listeners for on-prem caching servers: Azure Service Bus or SQS triggers a `git` refresh and a package sync. |
| `inventory/`, `enrollment/`, `intune/` | Inventory as the source of truth for Entra groups, manifests and Intune assignments (below). |
| `pkgsinfo/apps/managed/` | Sample Intune VPP app descriptors. |

## How a change reaches the fleet

1. An admin works on a branch in their own clone. `pre-commit` lints the
   pkgsinfo and runs `makecatalogs`; `post-merge` downloads the packages new
   pkgsinfo reference.
2. Pushing the branch uploads any package its pkgsinfo need, create-only and
   keyed by SHA-256, so the merge never references a package storage lacks.
   A package path is immutable: different bytes need a new path.
3. The pull request merges to `main`. The push pipeline rebuilds the catalogs
   with a pinned `makecatalogs`, refuses to publish if any pkgsinfo points at a
   package that is not in storage, syncs catalogs, manifests and pkgsinfo, and
   purges only the metadata paths at the CDN. Packages stay cached.
4. A message on Service Bus or SQS tells each caching server to pull `git` and
   sync packages and catalogs from storage.
5. On each Mac the preflight picks a caching server whose catalog is fresh, or
   the cloud, and points the Mac at its manifest from inventory.

Every pipeline signs in with workload identity federation or OIDC. There is no
client secret, SAS token or access key to store or rotate; every name in the
samples is a placeholder.

**Azure:** Blob Storage holds `<container>/deployment/{pkgs,icons,catalogs,manifests,pkgsinfo}`,
Front Door serves it, Service Bus notifies the caching servers.

**AWS:** S3 holds `<bucket>[/<prefix>]/deployment/...`, CloudFront serves it,
SQS notifies the caching servers.

The hooks, the pipelines and the listeners all use that same layout.

## makecatalogs and YAML

The pipelines expand a pinned [Munki](https://github.com/munki/munki) release
and run its `makecatalogs`. Upstream Munki reads plist pkgsinfo only. A repo
that keeps pkgsinfo in YAML needs a YAML-capable build
([munki/munki#1261](https://github.com/munki/munki/pull/1261)); point
`MUNKITOOLS_URL` and `MUNKITOOLS_SHA256` at it. The pipelines say so and fail
if they meet YAML pkgsinfo with a `makecatalogs` that cannot read it.

## Tests

CI (`.github/workflows/ci.yml`) runs everything offline: the Python tests, the
hook and preflight tests with fake cloud CLIs, a syntax pass over every script,
pipeline and plist, and a check that every GitHub Actions `uses:` is pinned to
a commit and that OIDC is granted only to jobs in a protected environment.

## Inventory, groups and manifests

The sections above are about how the *repo* works — storage split, hooks, catalogs,
CDN. Three further layers cover what the repo becomes a source of truth for:
Entra groups, configuration profiles, and App Store apps.

```
inventory/     the twelve-column contract, a sample fleet, and the projection script
enrollment/    one consumer per system; the Intune one builds the group ladder
intune/        renders three manifest keys the client ignores into Intune
```

**The idea.** Four inventory columns — `usage`, `catalog`, `area`, `location` —
are one hierarchy. At enrollment they become five nested Entra groups. Munki
manifests live in a directory tree built from the same columns. So:

```
manifests/Assigned/Staff/IT.yaml   <->   Devices-Assigned-Staff-IT
```

A manifest path and a group name are the same address written twice, and both
derive from the same row, so they cannot drift.

Which means a manifest can carry keys Munki ignores and have them mean something:

| Key | Renders to |
|---|---|
| `managed_apps` | VPP / App Store apps — the one thing Munki genuinely cannot install |
| `managed_profiles` | `.mobileconfig` and Settings Catalog / DDM policies |
| `managed_scripts` | Shell scripts |

One reviewed file describes what the agent does *and* what MDM does.

### Catalog-staged Intune releases

Machine-manifest `catalogs` identify an endpoint's cohort. Each profile, script,
and managed-app descriptor has a separate cumulative `catalogs` array that
declares how far that artifact has been promoted:

```yaml
catalogs:
- Development
- Testing
- Staging
```

Promotion is a reviewed edit to that array; it is never time-driven. The
pipeline automates only the mechanics: a changed profile or script becomes a
hash-identified candidate, the previous Production object remains assigned to
later cohorts, and the predecessor is retired only when the source explicitly
includes Production. A `.mobileconfig` carries its catalog array as a top-level
`Catalogs` key which the pipeline validates and strips before upload. Every
Apple payload's `PayloadVersion` must remain integer `1`; the candidate hash is
stored in Intune metadata instead. VPP identifiers and assignment metadata live
under `pkgsinfo/apps/managed/`, not in the pipeline body. New release-controller
logic stays inline in the pipeline YAML so deployment remains self-contained.

**Try it offline.** No tenant, no credentials, PyYAML the only dependency:

```
python3 inventory/projections/project.py inventory/inventory.csv --out-dir out/
cd enrollment && python3 -m consumers.intune ../out/intune.csv --what-if
cd ../intune && python3 -m stages.lint_conditions manifests/
python3 -m stages.plan_assignments manifests/
```

With no `GRAPH_TOKEN` the Intune consumer prints the group plan and stops. Run
that first, before handing it anything.

**Guards.** Adding is safe; removing is not. Every stage derives a desired state
by parsing something, and a degraded parse yields an *empty* desired set rather
than an error — a well-formed answer that removes everything on a green build.
So there is a floor on the desired set, a cap on how much one run may remove,
ownership markers so only this pipeline's own objects are touched, and `whatIf`
as a supported way to run. Read each layer's README before pointing it at a
tenant.

The Windows half of this pattern is
[cimian-gitops](https://github.com/windowsadmins/cimian-gitops): same keys, same
path-to-group rule, same guards, different render targets.

Want to talk shop or ask questions? Connect with me on [BlueSky](https://bsky.app/profile/rodchristiansen.net) or on the [Blog](https://focused.systems).

## License

MIT. See [LICENSE](LICENSE).
