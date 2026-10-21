# enrollment

One consumer per downstream system, each converging its own system towards the
inventory projection it was handed. No orchestrator, no ordering, no shared
state.

```
inventory.csv
   └── ../inventory/projections/project.py
         ├── intune.csv  ──> intune-gitops: consumers/intune.py   builds the group ladder
         ├── munki.csv   ──> consumers/munki.py                   publishes computers.csv
         ├── cimian.csv  ──> the cimian-gitops equivalent
         └── mdm.csv     ──> your ADE routing (no consumer ships here)
```

The shared parts (the naming convention, the Graph client, the guards, the
Intune group-ladder consumer and the triggers) live in
[windowsadmins/intune-gitops](https://github.com/windowsadmins/intune-gitops/tree/v0.1.5/enrollment),
together with their tests and the reasoning behind the guards. This directory
keeps only the Munki consumer, which imports `shared/` from there.

[cimian-gitops](https://github.com/windowsadmins/cimian-gitops) runs the same
projection for its Windows rows.

## Three properties that make this survivable

**Independent failure.** The blast radius of a bad deploy is one system. If the
print-server consumer is broken, Munki and Intune neither know nor care.

**No ordering.** Every consumer converges its own system towards the same row,
so the order they run in affects only how long the estate is briefly
inconsistent, not what it converges to.

**Idempotent by construction.** Every consumer is written as "make my system
match this row", never "apply this change". Re-run one twice and the second run
does nothing. You will replay these, usually at the worst moment, and a
consumer that applies deltas will happily apply the same delta twice.

## Setting up the path

Check intune-gitops out beside this repo at the tag the pipelines pin:

```
git clone --branch v0.1.5 https://github.com/windowsadmins/intune-gitops ../intune-gitops
```

Then put both enrollment directories on `PYTHONPATH`, **this repo's first**.
Both repos have a `consumers` package; this one extends its path to include
the intune-gitops one, which only works when it is found first:

```
export PYTHONPATH="$PWD/enrollment:$PWD/../intune-gitops/enrollment"
```

## Running one

Everything plans before it writes. Project the inventory, then plan each
consumer:

```
python3 inventory/projections/project.py inventory/inventory.csv --out-dir out/
```

```
python3 -m consumers.munki out/munki.csv --what-if
```

```
python3 -m consumers.intune out/intune.csv --what-if
```

With no `GRAPH_TOKEN` set, the Intune consumer prints the group plan and stops
without making a single call. That is the first thing to run after forking:
look at the ladder it would build before giving it credentials.

The Munki consumer refuses a projection whose header is not the published
column order, or with fewer rows than `MUNKI_MIN_ROWS`. Its publish steps
(push `computers.csv` to the repo host, upload it, purge the CDN path) are left
as integration points for your own hosting.

| Variable | Default | Meaning |
|---|---|---|
| `MUNKI_COMPUTERS_CSV_PATH` | `deployment/enroll/computers.csv` | Where the projection is published |
| `MUNKI_MIN_ROWS` | `1` | Floor below which the projection is refused; set it from your fleet size |

## Triggers

The intune-gitops triggers (an Azure Functions blob trigger and a signed
generic webhook) reach the built-in `intune` consumer. Register the Munki one
by name, with the `PYTHONPATH` above:

```
export ENROLLMENT_CONSUMERS="munki=consumers.munki:converge"
```

The intune-gitops `consumers` package extends its path, so `consumers.munki`
resolves from inside a trigger as well as when run directly.

## Tests

With `PYTHONPATH` set as above:

```
python3 -m unittest discover -s enrollment/tests -v
```

They cover the header check, the row floor, whatIf, and that `munki`
registers beside the shared `intune` consumer. The group-ladder tests run in
intune-gitops.

The projection test also runs `inventory/projections/project.py` on the sample
inventory and checks its `munki.csv` header is the one the consumer publishes.
