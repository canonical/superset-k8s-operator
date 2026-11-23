# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the Grafana dashboard rendering."""

import dataclasses
import json
from pathlib import Path

import pytest
from charms.grafana_k8s.v0.grafana_dashboard import LZMABase64
from ops.testing import Relation, StoredState

from dashboard import render_dashboard
from tests.unit.helpers import build_state

TEMPLATE = (
    Path(__file__).parents[2]
    / "src"
    / "dashboard_templates"
    / "superset-monitoring.json.tmpl"
)


def _variables(dashboard: dict) -> dict:
    """Return the dashboard variables keyed by name."""
    return {v["name"]: v for v in dashboard["templating"]["list"]}


def test_render_sets_model_defaults():
    """The model variables open on the given model."""
    dashboard = json.loads(render_dashboard(TEMPLATE, "my-model", "uuid-1"))
    variables = _variables(dashboard)

    for name, value in (
        ("juju_model", "my-model"),
        ("juju_model_uuid", "uuid-1"),
    ):
        assert variables[name]["current"] == {
            "text": [value],
            "value": [value],
        }


def test_render_keeps_other_models_selectable():
    """includeAll/multi stay on, and no other variable or panel changes."""
    original = json.loads(TEMPLATE.read_text())
    rendered = json.loads(render_dashboard(TEMPLATE, "my-model", "uuid-1"))

    for name in ("juju_model", "juju_model_uuid"):
        assert _variables(rendered)[name]["includeAll"] is True
        assert _variables(rendered)[name]["multi"] is True
        _variables(rendered)[name].pop("current", None)
        _variables(original)[name].pop("current", None)
    assert rendered == original


def test_render_missing_variable_raises(tmp_path):
    """A template without the model variables is rejected."""
    template = tmp_path / "dash.json"
    template.write_text(
        json.dumps({"templating": {"list": [{"name": "juju_unit"}]}})
    )

    with pytest.raises(ValueError, match="juju_model"):
        render_dashboard(template, "m", "u")


def test_charm_publishes_single_dashboard_for_its_model(ctx):
    """The charm sends one dashboard, defaulting to its own model."""
    relation = Relation("grafana-dashboard", interface="grafana_dashboard")
    state = build_state(extra_relations=[relation])

    out = ctx.run(ctx.on.config_changed(), state)

    out_relation = out.get_relation(relation.id)
    templates = json.loads(out_relation.local_app_data["dashboards"])[
        "templates"
    ]
    assert len(templates) == 1
    dashboard = json.loads(
        LZMABase64.decompress(next(iter(templates.values()))["content"])
    )
    expected = [out.model.name]
    assert _variables(dashboard)["juju_model"]["current"]["value"] == expected
    # Same uid the lib generated when the template was loaded from the old dir.
    assert dashboard["uid"] == "00112546513c0282bb384c67dea4c9c10259fabc"


def test_charm_drops_stale_dashboard_from_stored_state(ctx):
    """A dashboard kept in stored state from an earlier render is not published."""
    stale = {
        "charm": "superset-k8s",
        "content": "x",
        "juju_topology": {},
        "inject_dropdowns": True,
        "dashboard_alt_uid": "stale",
    }
    stored = StoredState(
        owner_path="SupersetK8SCharm/GrafanaDashboardProvider[grafana-dashboard]",
        name="_stored",
        content={"dashboard_templates": {"prog:STALE123": stale}},
    )
    relation = Relation("grafana-dashboard", interface="grafana_dashboard")
    state = dataclasses.replace(
        build_state(extra_relations=[relation]), stored_states={stored}
    )

    out = ctx.run(ctx.on.update_status(), state)

    templates = json.loads(
        out.get_relation(relation.id).local_app_data["dashboards"]
    )["templates"]
    assert len(templates) == 1
    assert "prog:STALE123" not in templates
