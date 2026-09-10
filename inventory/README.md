# inventory

One row per device, twelve columns, in whatever system your organisation already
edits. Everything downstream is a projection of this.

The product holding these columns is an implementation detail. Snipe-IT, Jamf
Pro inventory, ServiceNow, a Postgres view, a spreadsheet exported on a
schedule — all satisfy the contract. What matters is that there is exactly one
place a human edits the description of a device, and that everyone agrees what
the columns mean.

## The column contract

| Column | Decides |
|---|---|
| `serial` | The join key. Every downstream system keys on this. |
| `asset` | The asset tag on the sticker — what a human quotes at you. |
| `platform` | Which agent owns the machine: `Macintosh` or `Windows`. |
| `catalog` | The software tier: Staff, Faculty, Curriculum, Kiosk, Provisioning. |
| `area` | The department or functional grouping. |
| `location` | The room. |
| `usage` | `Assigned` (one person) or `Shared` (many). The most load-bearing column. |
| `status` | Active, Retired, Lost, New. Decides whether the device should exist in any system. |
| `allocation` | Human-readable "who or what this is" — a person, or a lab bench. |
| `username` | The account that gets the primary-user association. Empty on shared devices. |
| `hostname` | What the machine calls itself. |
| `fleet` | Optional sub-grouping for anything the above doesn't capture. |

`usage`, `catalog`, `area` and `location` are four levels of one hierarchy, in
that order, broadest to narrowest. That ordering is the whole architecture:

```
inventory columns   usage / catalog / area / location
Entra group ladder  Devices-{usage}-{catalog}-{area}-{location}
manifest path       manifests/{usage}/{catalog}/{area}.yaml
```

A group name and a manifest path are the same address written twice.

## The vocabulary is closed

`catalog`, `area`, `usage` and `status` are closed value sets, not free text.

A stray value does not raise an error. It creates a new Entra group with one
device in it and a manifest path that resolves to nothing, and the device
quietly stops receiving software. Renaming a value is a migration with an
order — create the new value everywhere, move the rows, confirm devices landed,
retire the old value — not an edit.

Treat the vocabulary like a database schema. It is acting as a foreign key
across every system you manage.

## What is deliberately absent

- **Hardware facts** (model, CPU, RAM, storage). Every management system
  collects these better than a human types them. If it is discoverable, it is
  not inventory's job.
- **Compliance and patch state.** A fact about right now, not a description of
  the device. That belongs in reporting.
- **Group names, manifest paths, policy IDs.** These are derived. Storing a
  derived value creates a second source of truth, which is the problem this
  whole kit exists to remove.
- **Purchase and lease data.** Real, and a different projection of the same
  asset for a different consumer — not part of this contract.

## Projecting

```
python3 projections/project.py inventory.csv --out-dir out/
```

Writes one narrowed CSV per target into `out/`. Each target receives only the
columns it can act on, and platform-specific targets receive only their own
rows:

| Target | Rows | Consumed by |
|---|---|---|
| `munki.csv` | Macintosh | `../enrollment/consumers/munki.py` |
| `cimian.csv` | Windows | the Cimian repo's equivalent consumer |
| `intune.csv` | all | `../enrollment/consumers/intune.py` — builds the group ladder |
| `mdm.csv` | all | `../enrollment/consumers/mdm.py` — ADE / Autopilot routing |

Add a target by adding an entry to `COLUMNS` in `project.py`, and to `PLATFORM`
if it only wants one platform.

## Sample data

`inventory.csv` is an invented fleet — made-up serials, asset tags, rooms and
people, at `example.ca`. It covers both platforms, both usages, five catalogs
and four rooms, so the group ladder it produces is worth looking at.

Before publishing your own version of any export like this, grep for: names in
file paths, serial numbers, asset tags, tenant and subscription IDs, repository
GUIDs, storage account names, service connection names, webhook URLs and
internal hostnames.
