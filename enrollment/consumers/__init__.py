"""Munki's consumer, alongside the shared ones in intune-gitops.

intune-gitops also has a `consumers` package (the group ladder and the
registry). Extending __path__ lets `consumers.munki` and `consumers.intune`
both resolve when this directory comes first on PYTHONPATH.
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
