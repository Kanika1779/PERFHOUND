import re
import subprocess
from pathlib import Path

import pytest

from perfhound.gateway import Gateway, RegressionCase
from perfhound.gateway import local_git
from perfhound.gateway.errors import GatewayError
from perfhound.gateway.fetcher import FetchError, RepoFetcher, local_dir_for, validate_url
from perfhound.gateway.gitcmd import is_partial_clone

from .fixture_repo import BAD_TAG, GOOD_TAG


@pytest.fixture
def remote(fresh_fixture_repo):
    """A fixture repo that serves partial clones over file://, like GitHub does over https."""
    r = fresh_fixture_repo
    r.git("config", "uploadpack.allowFilter", "true")
    r.git("config", "uploadpack.allowAnySHA1InWant", "true")
    r.url = r.path.as_uri()
    return r


@pytest.mark.parametrize("url", [
    "ext::sh -c touch% /tmp/pwned", "fd::17", "-oProxyCommand=evil", "ftp://example.com/x.git",
])
def test_dangerous_or_unknown_urls_are_refused(url):
    with pytest.raises(FetchError):
        validate_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com/pandas-dev/pandas", "https://github.com/pandas-dev/pandas.git",
    "git@github.com:pandas-dev/pandas.git", "ssh://git@github.com/pandas-dev/pandas",
])
def test_github_urls_map_to_one_folder(url, tmp_path):
    validate_url(url)
    assert local_dir_for(url, tmp_path) == tmp_path / "github.com" / "pandas-dev" / "pandas"


def test_local_path_is_used_as_is(fixture_repo, tmp_path):
    f = RepoFetcher(tmp_path / "repos")
    assert f.fetch(str(fixture_repo.path)) == fixture_repo.path
    assert not (tmp_path / "repos").exists()
    with pytest.raises(FetchError):
        f.fetch(str(tmp_path / "missing"))


def test_clone_is_partial_without_checkout_and_reused(remote, tmp_path):
    f = RepoFetcher(tmp_path / "repos")
    path = f.fetch(remote.url)
    assert is_partial_clone(path)
    assert [p.name for p in path.iterdir()] == [".git"]            # no working files
    assert f.fetch(remote.url) == path                              # second call: no new clone
    assert not [p for p in path.parent.iterdir() if ".tmp-" in p.name]


def test_failed_clone_leaves_nothing(tmp_path):
    f = RepoFetcher(tmp_path / "repos")
    (tmp_path / "repos").mkdir()
    with pytest.raises(FetchError, match="could not clone"):
        f.fetch((tmp_path / "no_such_repo").as_uri())
    assert not [p for p in (tmp_path / "repos").rglob("*") if ".tmp-" in p.name]


def test_ensure_commits_fetches_new_history(remote, tmp_path):
    f = RepoFetcher(tmp_path / "repos")
    path = f.fetch(remote.url)
    remote.git("commit", "-q", "--allow-empty", "-m", "pushed later")
    new_sha = remote.git("rev-parse", "HEAD")
    assert f.ensure_commits(path, [GOOD_TAG, new_sha]) == []
    assert f.ensure_commits(path, ["0" * 40]) == ["0" * 40]


def _lazy_fetches(trace: Path) -> int:
    if not trace.exists():
        return 0
    return len(re.findall(r"run_command: .*fetch origin", trace.read_text(encoding="utf-8", errors="replace")))


@pytest.mark.parametrize("prefetch", [True, False])
def test_partial_clone_blobs_come_in_one_batch(remote, tmp_path, monkeypatch, prefetch):
    case = RegressionCase(case_id="t:1", source="test", repo=remote.url, language="python",
                          good=GOOD_TAG, bad=BAD_TAG)
    gw = Gateway.for_case(case, fetcher=RepoFetcher(tmp_path / "repos"), cache=False)
    if not prefetch:   # control experiment: prove the test can see lazy fetches
        monkeypatch.setattr(local_git, "prefetch_blobs", lambda repo, shas: 0)
    trace = tmp_path / "git_trace.txt"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    got = gw.candidates_for(case)
    monkeypatch.delenv("GIT_TRACE")

    expected = Gateway(remote.path, cache=False).get_candidates(GOOD_TAG, BAD_TAG)
    assert got == expected                       # same data as from the full repo
    if prefetch:
        assert _lazy_fetches(trace) == 0
    else:
        assert _lazy_fetches(trace) > 1


def test_for_case_reports_missing_commits(remote, tmp_path):
    case = RegressionCase(case_id="t:2", source="test", repo=remote.url, language="python",
                          good=GOOD_TAG, bad="f" * 40)
    with pytest.raises(GatewayError, match="not found"):
        Gateway.for_case(case, fetcher=RepoFetcher(tmp_path / "repos"), cache=False)


def test_stale_half_clones_are_removed(remote, tmp_path):
    import os, time
    f = RepoFetcher(tmp_path / "repos")
    dest = local_dir_for(remote.url, tmp_path / "repos")
    dest.parent.mkdir(parents=True)
    old = dest.parent / f".{dest.name}.tmp-dead0001"
    young = dest.parent / f".{dest.name}.tmp-busy0002"
    for p in (old, young):
        (p / "objects").mkdir(parents=True)
    os.utime(old, (time.time() - 7 * 3600,) * 2)
    f.fetch(remote.url)
    assert not old.exists() and young.exists()


def test_relative_base_dir_works(remote, tmp_path, monkeypatch):
    """Found on the first real multi-repo run: base_dir="repos" cloned into repos/repos/..."""
    monkeypatch.chdir(tmp_path)
    path = RepoFetcher("repos").fetch(remote.url)
    assert path.is_absolute() and (path / ".git").exists()
    assert path == (tmp_path / "repos").resolve() / path.relative_to((tmp_path / "repos").resolve())
