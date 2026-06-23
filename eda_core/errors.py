"""Shared exception types for the review core."""

from __future__ import annotations


class PartSourceError(RuntimeError):
    """Raised when a part cannot be resolved, pulled, or sourced."""
