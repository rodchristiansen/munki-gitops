"""Azure Functions doorbell for the enrollment consumers.

The blob trigger is an Azure detail. The consumer logic is not -- it lives in
../../consumers/ and imports nothing cloud-specific, which is what lets the same
code run behind the plain HTTP handler in ../generic-webhook/.

One consumer per landing blob: no orchestrator, no ordering, no shared state.
The blast radius of a bad deploy is one system.
"""
from __future__ import annotations

import pathlib
import sys

import azure.functions as func

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from consumers import intune, munki  # noqa: E402

CONTAINER = "inventory-data/production"

app = func.FunctionApp()


@app.blob_trigger(arg_name="blob", path=f"{CONTAINER}/intune.csv",
                  connection="STORAGE_CONNECTION")
def on_intune_csv(blob: func.InputStream) -> None:
    intune.converge(blob.read().decode("utf-8"))


@app.blob_trigger(arg_name="blob", path=f"{CONTAINER}/munki.csv",
                  connection="STORAGE_CONNECTION")
def on_munki_csv(blob: func.InputStream) -> None:
    munki.converge(blob.read().decode("utf-8"))
