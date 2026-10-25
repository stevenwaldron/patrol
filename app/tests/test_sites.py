from pathlib import Path

import pytest

from patrol.sites import load_sites, parse_sites

REPO_SITES = Path(__file__).resolve().parents[2] / "config" / "sites.yaml"


def test_repo_sites_file_is_valid():
    sites = load_sites(str(REPO_SITES))
    ids = [s.id for s in sites]
    assert "broken-test" in ids
    assert len(ids) == len(set(ids))


def test_defaults_applied():
    [site] = parse_sites({"sites": [{"id": "a", "url": "https://a.example"}]})
    assert site.name == "a" and site.timeout_seconds == 10


@pytest.mark.parametrize("entry, message", [
    ({"id": "Bad_ID", "url": "https://x.example"}, "lowercase"),
    ({"id": "ok", "url": "ftp://x.example"}, "http"),
    ({"id": "ok", "url": "https://x.example", "timeout_seconds": 99}, "between 1 and 30"),
])
def test_invalid_entries_rejected(entry, message):
    with pytest.raises(ValueError, match=message):
        parse_sites({"sites": [entry]})


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        parse_sites({"sites": [{"id": "a", "url": "https://a.example"},
                               {"id": "a", "url": "https://b.example"}]})


def test_empty_file_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        parse_sites({})
