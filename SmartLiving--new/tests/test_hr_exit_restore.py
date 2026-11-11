from unittest.mock import MagicMock, patch

from bson import ObjectId

from hr_backend import hr_exits


def test_restore_archived_user_moves_document_and_keeps_login_blocked():
    user_id = ObjectId()
    archived = {"_id": user_id, "username": "former.agent", "role": "agent", "password": "hash"}
    exited = MagicMock()
    exited.find_one.return_value = archived
    users = MagicMock()
    users.find_one.side_effect = [None, None, {"_id": user_id}]

    with patch.object(hr_exits, "exited_users_col", exited), patch.object(hr_exits, "users_col", users):
        ok, _message = hr_exits._restore_exited_user_to_users(user_id, login_enabled=False)

    assert ok is True
    restored = users.replace_one.call_args.args[1]
    assert restored["employment_status"] == "Exited"
    assert restored["is_active"] is False
    assert restored["account_locked"] is True
    exited.delete_one.assert_called_once_with({"_id": user_id})


def test_restore_archived_user_enables_login_when_requested():
    user_id = ObjectId()
    exited = MagicMock()
    exited.find_one.return_value = {"_id": user_id, "username": "former.manager", "role": "manager"}
    users = MagicMock()
    users.find_one.side_effect = [None, None, {"_id": user_id}]

    with patch.object(hr_exits, "exited_users_col", exited), patch.object(hr_exits, "users_col", users):
        ok, _message = hr_exits._restore_exited_user_to_users(user_id, login_enabled=True)

    assert ok is True
    restored = users.replace_one.call_args.args[1]
    assert restored["is_active"] is True
    assert restored["account_locked"] is False


def test_restore_rejects_duplicate_username():
    user_id = ObjectId()
    exited = MagicMock()
    exited.find_one.return_value = {"_id": user_id, "username": "duplicate", "role": "agent"}
    users = MagicMock()
    users.find_one.side_effect = [None, {"_id": ObjectId()}]

    with patch.object(hr_exits, "exited_users_col", exited), patch.object(hr_exits, "users_col", users):
        ok, message = hr_exits._restore_exited_user_to_users(user_id, login_enabled=True)

    assert ok is False
    assert "username" in message.lower()
    users.replace_one.assert_not_called()
    exited.delete_one.assert_not_called()
