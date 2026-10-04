import pytest

from .fixture_repo import FixtureRepo, build_fixture_repo


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory) -> FixtureRepo:
    """Shared, READ-ONLY fixture repo (built once per test session).

    Tests that modify the repo (checkout, worktrees...) must use
    `fresh_fixture_repo` instead.
    """
    return build_fixture_repo(tmp_path_factory.mktemp("fixture_repo"))


@pytest.fixture
def fresh_fixture_repo(tmp_path) -> FixtureRepo:
    """A private copy of the fixture repo for tests that mutate it."""
    return build_fixture_repo(tmp_path / "repo")
