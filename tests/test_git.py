"""Tests for vault Git auto-commit integration."""

import pytest
from pathlib import Path
from noteweaver.vault import Vault


@pytest.fixture
def git_vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=True)
    v.init()
    return v


class TestGitIntegration:
    def test_init_creates_git_repo(self, git_vault: Vault) -> None:
        assert (git_vault.root / ".git").is_dir()

    def test_init_creates_initial_commit(self, git_vault: Vault) -> None:
        from git import Repo
        repo = Repo(git_vault.root)
        commits = list(repo.iter_commits())
        assert len(commits) >= 1
        assert "Vault initialized" in commits[0].message

    def test_write_creates_commit(self, git_vault: Vault) -> None:
        from git import Repo
        repo = Repo(git_vault.root)
        before = len(list(repo.iter_commits()))

        git_vault.write_file("wiki/concepts/test.md", "# Test")

        after = len(list(repo.iter_commits()))
        assert after > before

    def test_gitignore_excludes_meta(self, git_vault: Vault) -> None:
        gitignore = (git_vault.root / ".gitignore").read_text()
        assert ".meta/" in gitignore

    def test_append_log_creates_commit(self, git_vault: Vault) -> None:
        from git import Repo
        repo = Repo(git_vault.root)
        before = len(list(repo.iter_commits()))

        git_vault.append_log("test", "Log commit test")

        after = len(list(repo.iter_commits()))
        assert after > before
        latest_msg = list(repo.iter_commits())[0].message
        assert "Log:" in latest_msg

    def test_auto_git_false_skips_git(self, tmp_path: Path) -> None:
        v = Vault(tmp_path, auto_git=False)
        v.init()
        assert not (tmp_path / ".git").is_dir()
