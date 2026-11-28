# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Rendering of the Grafana dashboard shipped by the Superset charm."""

import json
from pathlib import Path
from typing import Optional

from cosl import DashboardPath40UID

# Template variables whose initial selection is the charm's own model.
# Other values stay selectable since includeAll/multi are left untouched.
_MODEL_VARIABLES = ("juju_model", "juju_model_uuid")


def render_dashboard(
    template_path: Path,
    model_name: str,
    model_uuid: str,
    charm_name: Optional[str] = None,
) -> str:
    """Render the dashboard so that it opens on the given Juju model.

    Args:
        template_path: path of the dashboard JSON template.
        model_name: name of the Juju model the charm is deployed in.
        model_uuid: uuid of the Juju model the charm is deployed in.
        charm_name: if given, the dashboard uid is set to the one the
            grafana_dashboard lib generated when the template was loaded from
            src/grafana_dashboards/, so that it stays the same on upgrade.

    Returns:
        The dashboard JSON, with the model variables defaulting to the model.

    Raises:
        ValueError: if the template lacks one of the model variables.
    """
    dashboard = json.loads(template_path.read_text())
    defaults = {"juju_model": model_name, "juju_model_uuid": model_uuid}
    variables = {v.get("name"): v for v in dashboard["templating"]["list"]}
    for name in _MODEL_VARIABLES:
        if name not in variables:
            raise ValueError(f"dashboard template has no {name!r} variable")
        variables[name]["current"] = {
            "text": [defaults[name]],
            "value": [defaults[name]],
        }
    if charm_name is not None:
        dashboard["uid"] = DashboardPath40UID.generate(
            charm_name, f"src/grafana_dashboards/{template_path.name}"
        )
    return json.dumps(dashboard)
