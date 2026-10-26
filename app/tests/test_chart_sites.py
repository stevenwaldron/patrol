"""The cluster's site list (Helm values) must match the local one (config/sites.yaml).

Two copies exist on purpose: Helm can only read files inside the chart folder,
and local runs/images use config/sites.yaml. This test stops them drifting apart.
"""

from pathlib import Path

import yaml

from patrol.sites import parse_sites

REPO = Path(__file__).resolve().parents[2]


def test_chart_sites_match_config_sites():
    chart_values = yaml.safe_load((REPO / "deploy/charts/patrol/values.yaml").read_text())
    local = yaml.safe_load((REPO / "config/sites.yaml").read_text())

    # Both must also pass the app's own validation.
    assert parse_sites({"sites": chart_values["sites"]}) == parse_sites(local)
