"""An in-memory stand-in for GraphClient.

Same method names, a dictionary instead of a tenant. Lets the group-ladder
convergence be tested for the things that actually frighten us -- a collapsed
parse emptying every group, a runaway removal -- in under a second, with no
network and no credentials.
"""
from __future__ import annotations


class FakeGraph:
    def __init__(self, devices: dict[str, str] | None = None, what_if: bool = False,
                 token: str = "fake-token"):
        # serial -> azureADDeviceId
        self.devices = devices or {}
        self.what_if = what_if
        # Truthy so the consumer takes the converge path rather than the
        # offline plan-only path. Set token="" to exercise planning.
        self.token = token
        self.groups: dict[str, str] = {}          # name -> id
        self.members: dict[str, set[str]] = {}    # group id -> object ids
        self.created: list[str] = []
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []
        self._next = 0

    # -- shape used by the consumer ---------------------------------------

    def paged(self, url: str) -> list[dict]:
        if "managedDevices" in url:
            return [
                {
                    "id": f"md-{serial}",
                    "azureADDeviceId": oid,
                    "serialNumber": serial,
                    "managementState": "managed",
                    "enrolledDateTime": "2026-01-01T00:00:00Z",
                }
                for serial, oid in self.devices.items()
            ]
        return []

    def group_id(self, name: str) -> str | None:
        return self.groups.get(name)

    def ensure_group(self, name: str, description: str) -> str:
        if name not in self.groups:
            self._next += 1
            self.groups[name] = f"gid-{self._next}"
            self.members[self.groups[name]] = set()
            self.created.append(name)
        return self.groups[name]

    def group_member_ids(self, group_id: str) -> set[str]:
        return set(self.members.get(group_id, set()))

    def add_member(self, group_id: str, object_id: str) -> None:
        if self.what_if:
            return
        self.members.setdefault(group_id, set()).add(object_id)
        self.added.append((group_id, object_id))

    def remove_member(self, group_id: str, object_id: str) -> None:
        if self.what_if:
            return
        self.members.setdefault(group_id, set()).discard(object_id)
        self.removed.append((group_id, object_id))

    # -- helpers for assertions -------------------------------------------

    def members_of(self, name: str) -> set[str]:
        gid = self.groups.get(name)
        return set(self.members.get(gid, set())) if gid else set()

    def preload(self, name: str, object_ids: set[str]) -> None:
        gid = self.ensure_group(name, "")
        self.members[gid] = set(object_ids)
