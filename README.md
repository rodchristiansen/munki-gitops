# Modern Munki DevOps

**MacDevOps YVR 2025 presentation Companion Repo** - [YouTube link](https://www.youtube.com/watch?v=ayQqGT9S_cM&t=6s&pp=ygUQcm9kIGNocmlzdGlhbnNlbg%3D%3D)

A year ago, we were managing Macs the way most orgs still do: one shared Mac, VNC’d into, running a local copy of Munki with no real version control or workflow isolation. Git was an afterthought. Deployments were manual. It worked—until it didn’t scale.

We’ve since inverted the model. Git is the gate. CI is the deployer. Each admin works from their own machine. And the Munki repo is fully DevOps-native.

Here's how we rebuilt everything using Git, CI/CD pipelines, message queues, local caching servers, and inventory automation—all open source and cloud-integrated.

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

Want to talk shop or ask questions? Connect with me on [BlueSky](https://bsky.app/profile/rodchristiansen.net) or on the [Blog](https://focused.systems).
