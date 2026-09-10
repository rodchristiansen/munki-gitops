"""A cloud-free doorbell for the same consumers.

    pip install flask && python3 app.py
    curl -X POST --data-binary @out/intune.csv http://localhost:8080/intune

Exists to make the point that nothing in consumers/ needs a Functions host. A
cron box, a GitHub Action, or this file are all equally valid front doors.
"""
from __future__ import annotations

import pathlib
import sys

from flask import Flask, request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from consumers import intune, munki  # noqa: E402

app = Flask(__name__)

CONSUMERS = {"intune": intune.converge, "munki": munki.converge}


@app.post("/<target>")
def receive(target: str):
    converge = CONSUMERS.get(target)
    if converge is None:
        return {"error": f"unknown target {target}"}, 404
    code = converge(request.get_data().decode("utf-8"))
    return {"target": target, "exit": code}, (200 if code == 0 else 500)


if __name__ == "__main__":
    app.run(port=8080)
