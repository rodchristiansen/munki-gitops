# Modern Munki DevOps

> **MacDevOps YVR 2025 Companion Post**

A year ago, we were managing Macs the way most orgs still do: one shared Mac, VNC’d into, running a local copy of Munki with no real version control or workflow isolation. Git was an afterthought. Deployments were manual. It worked—until it didn’t scale.

We’ve since inverted the model. Git is the gate. CI is the deployer. Each admin works from their own machine. And the Munki repo is fully DevOps-native.

Here’s how we rebuilt everything using Git, Azure DevOps, Service Bus, pipelines, local caching servers, and inventory automation—all open source and cloud-integrated.


## From Manual to DevOps

The legacy flow was:

- GitLab running on-prem
- One shared Mac as the deploy point
- One central repo, updated by many hands
- No pipeline. No hooks. No approval gates.
- Everyone stepped on everyone’s toes

Now we have:

- Azure DevOps Git repos and pipelines
- Git hooks that upload/download packages automatically
- Separate working copies per admin
- Local caching servers that sync intelligently
- A full CI/CD system that integrates with inventory and deploys via pull requests


## Architecture Overview

We’ve split this into two core flows:

### Munki DevOps Infrastructure

- Admins commit to a shared Azure DevOps repo with `manifests/` and `pkgsinfo/`
- Git hooks (post-commit/merge) run `azcopy sync` to upload or download packages
- A pipeline (`munki-push-production.yml`) builds catalogs and updates Azure Storage
- Local caching servers (like PLUTO and PROTEUS) are notified via Azure Service Bus
- A daemon listens for commits and runs `git pull` and syncs assets
- CDN serves files globally or from on-prem caches
- It’s fast, redundant, and observable

### Inventory-Orchestrated Enrollment

- A polling script checks Snipe-IT for inventory changes and builds new CSVs
- CSVs are committed to Git → triggers DevOps pipelines → runs `enrollment-munki`, `enrollment-intune`, `enrollment-sharepoint`, and more
- Each system (Munki, Intune, Fleet, TDX, Papercut) gets updated data
- Pull requests are the approval gate
- Everything is traceable, auditable, and CI-driven


Want to talk shop or ask questions? Connect with me on [BlueSky](https://bsky.app/profile/rodchristiansen.net) or on the [Blog](https://focused.systems).
