"""Job sources. Each module exposes `fetch(cfg) -> list[JobPosting]`.

A source that errors or is unconfigured returns an empty list (and prints a note) rather than
aborting the weekly run — discover.py isolates per-source failures.
"""
