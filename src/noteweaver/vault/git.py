"""Git integration for the vault: init, commit, operation batching."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noteweaver.vault.core import Vault

log = logging.getLogger(__name__)


def git_init(vault: Vault) -> None:
    """Initialize a git repo in the vault if auto_git is enabled."""
    if not vault._auto_git:
        return
    try:
        from git import Repo, InvalidGitRepositoryError
        try:
            vault._repo = Repo(vault.root)
        except InvalidGitRepositoryError:
            vault._repo = Repo.init(vault.root)
            vault._repo.config_writer().set_value(
                "user", "name", "NoteWeaver"
            ).release()
            vault._repo.config_writer().set_value(
                "user", "email", "agent@noteweaver"
            ).release()
    except ImportError:
        log.debug("gitpython not installed, git auto-commit disabled")
        vault._auto_git = False


def git_commit(vault: Vault, message: str) -> None:
    """Stage all changes and commit if there are any."""
    if not vault._auto_git or vault._repo is None:
        return
    try:
        vault._repo.git.add(A=True)
        if vault._repo.is_dirty(untracked_files=True):
            vault._repo.index.commit(message)
    except Exception as e:
        log.warning("git commit failed: %s", e)


class OperationContext:
    """Batches all vault writes into a single git commit.

    Supports nesting: only the outermost context triggers the commit.
    """

    def __init__(self, vault: Vault, message: str) -> None:
        self._vault = vault
        self._message = message

    def __enter__(self) -> Vault:
        self._vault._operation_depth += 1
        return self._vault

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._vault._operation_depth -= 1
        if self._vault._operation_depth == 0 and self._vault._operation_dirty:
            git_commit(self._vault, self._message)
            self._vault._operation_dirty = False
        return None
