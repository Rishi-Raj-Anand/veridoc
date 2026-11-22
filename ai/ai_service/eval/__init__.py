"""Offline evaluation (CLAUDE.md §13): the test set, the RAGAS adapter and the runner.

RAGAS sends usage analytics unless ``RAGAS_DO_NOT_TRACK`` is ``true``, and it reads (and
caches) that flag on first use. This package sets it before any of its modules can import
``ragas``, because the project allows no telemetry (§16). It is a flag for a third-party
library, not project configuration, so it does not belong in ``config.py``.
"""

import os

RAGAS_DO_NOT_TRACK = "RAGAS_DO_NOT_TRACK"

os.environ[RAGAS_DO_NOT_TRACK] = "true"
