from src.tools.git_tools import clone_repo


def test_clone_repo_requires_dedicated_destination():
    result = clone_repo("https://github.com/example/repo")

    assert "destination" in result.lower()
