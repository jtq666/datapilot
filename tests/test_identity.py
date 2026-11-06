import pytest

from data_platform.identity.store import IdentityStore


def test_password_session_and_workspace_boundary(tmp_path):
    store = IdentityStore(tmp_path / "identity.db")
    alice = store.create_user("alice", "correct-long-password")
    bob = store.create_user("bob", "another-long-password")
    with pytest.raises(ValueError):
        store.create_user("short", "bad")
    assert store.login("alice", "wrong") is None
    token = store.login("alice", "correct-long-password")
    assert store.user_for_token(token) == alice
    workspace = store.create_workspace(alice, "Research")
    assert store.role(alice, workspace) == "owner"
    assert store.role(bob, workspace) is None
    with pytest.raises(PermissionError):
        store.add_member(bob, workspace, bob)
    store.add_member(alice, workspace, bob)
    assert store.role(bob, workspace) == "member"
    store.logout(token)
    assert store.user_for_token(token) is None


def test_expired_session(tmp_path):
    store = IdentityStore(tmp_path / "identity.db")
    store.create_user("alice", "correct-long-password")
    token = store.login("alice", "correct-long-password", lifetime_seconds=-1)
    assert store.user_for_token(token) is None
