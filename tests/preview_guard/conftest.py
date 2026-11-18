# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for testing templates/mcp_preview_guard.py.

That module ships inside the workload image (pushed by
load_superset_files()) rather than imported by the charm, so it is not
on the charm's own PYTHONPATH; it is added to sys.path here instead.

The directory is deliberately not named after the module: pytest puts
a test package's parent on sys.path, so a `tests/mcp_preview_guard/`
package would be imported as `mcp_preview_guard` and shadow the module
under test.
"""

import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parents[2] / "templates"
sys.path.insert(0, str(TEMPLATES_DIR))
