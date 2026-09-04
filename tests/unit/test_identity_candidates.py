# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for _identity_candidates in templates/superset_config.py.

The function lives in templates/superset_config.py, loaded by every
Superset process via PYTHONPATH. The rest of that file needs
flask_appbuilder/superset/celery, none of which this env carries, so
only this one marker-delimited block is extracted and exec'd -- same
approach as test_cache_key_patch.py uses for its own patch functions.
_identity_candidates has no imports of its own, so no stubbing is
needed to call it.
"""

import pathlib


def _load():
    """Exec only the _identity_candidates block from superset_config.py.

    Returns:
        The _identity_candidates function.
    """
    config_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "templates"
        / "superset_config.py"
    )
    src = config_path.read_text()
    start = src.index("def _identity_candidates(")
    end = src.index("# End _identity_candidates")
    ns: dict = {}
    exec(src[start:end], ns)  # nosec B102  # pylint: disable=exec-used
    return ns["_identity_candidates"]


_identity_candidates = _load()


def test_hydra_prefers_sub():
    """Hydra's client_credentials sub is the Superset username directly."""
    assert _identity_candidates("oidc", {"sub": "admin_client"}) == [
        "admin_client"
    ]


def test_google_prefers_email_over_sub():
    """Google's sub is a numeric account id, never a Superset username."""
    claims = {"sub": "108234821374", "email": "ia@example.com"}
    assert _identity_candidates("google", claims) == [
        "ia@example.com",
        "108234821374",
    ]


def test_falls_back_to_client_id_last():
    """A token with no sub or email still resolves via the client_id."""
    assert _identity_candidates("oidc", {}, "svc-account") == ["svc-account"]


def test_deduplicates():
    """The same identity is not listed twice."""
    assert _identity_candidates("oidc", {"sub": "x"}, "x") == ["x"]


def test_no_candidates_is_empty():
    """A token with nothing usable yields no candidates."""
    assert _identity_candidates("oidc", {}) == []
