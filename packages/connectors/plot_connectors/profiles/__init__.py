"""Source-profile registry (Phase 6 §6.1.B.3).

The MVP must-have sources (§32) configured as :class:`SourceProfile` instances of the
generic adapters. Profiles are *config*: their endpoints need not be reachable to build
the registry. Each profile carries endpoint, layer/dataset names, ``legal_status``,
``license``, and a fallback note (§28 fallback documentation).
"""

from __future__ import annotations

from plot_connectors.profiles.PL import (
    PL_PROFILES,
    get_profile,
    list_profiles,
)

__all__ = ["PL_PROFILES", "get_profile", "list_profiles"]
