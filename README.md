# Running Munki under DevOps model

**MacDevOps YVR 2025 presentation Companion Repo** - [YouTube link](https://www.youtube.com/watch?v=ayQqGT9S_cM&t=6s&pp=ygUQcm9kIGNocmlzdGlhbnNlbg%3D%3D)

Repo has samples of how we rebuilt our Munki ops to be fully Git with hooks, CI/CD pipelines, message queues, and local caching servers.

**Cloud Provider Options**: This repo includes implementations for both **Azure** (Azure DevOps, Azure Storage, Service Bus) and **AWS** (GitHub Actions/CodePipeline, S3, SQS/SNS). Choose the cloud provider that fits your infrastructure.


## From Manual to DevOps

The legacy flow was:

- GitLab running on-prem
- One shared Mac as the deploy point
- One central repo, updated by many hands
- No pipeline. No hooks. No approval gates.
- Everyone stepped on everyone’s toes

Now we have:

- Git repos and CI/CD pipelines (Azure DevOps or GitHub Actions/AWS CodePipeline)
- Git hooks that upload/download packages automatically (Azure Storage or S3)
- Separate working copies per admin
- Local caching servers that sync intelligently
- A full CI/CD system that integrates with inventory and deploys via pull requests


## Architecture Overview

We’ve split this into two core flows:

### Munki DevOps Infrastructure

**Azure Implementation:**
- Admins commit to a shared Azure DevOps repo with `manifests/` and `pkgsinfo/`
- Git hooks (post-commit/merge) run `azcopy sync` to upload or download packages
- A pipeline (`munki-push-production.yml`) builds catalogs and updates Azure Storage
- Local caching servers are notified via Azure Service Bus
- A daemon listens for commits and runs `git pull` and syncs assets
- CDN serves files globally or from on-prem caches

**AWS Implementation:**
- Admins commit to a GitHub repo (or AWS CodeCommit) with `manifests/` and `pkgsinfo/`
- Git hooks run `aws s3 sync` to upload or download packages
- A pipeline (GitHub Actions or CodePipeline) builds catalogs and updates S3
- Local caching servers are notified via SQS/SNS
- A daemon listens for messages and runs `git pull` and syncs assets
- CloudFront serves files globally or from on-prem caches

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
