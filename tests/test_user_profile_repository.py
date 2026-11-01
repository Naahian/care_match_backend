from unittest.mock import Mock

from app.repositories.user_profile import UserProfileRepository
from app.schemas.users import UserUpdate


def test_personal_profile_merge_uses_uid_and_serializable_dates():
    db = Mock()
    repo = UserProfileRepository(db)
    changes = UserUpdate(date_of_birth="2000-02-29", blood_group="o+").model_dump(
        mode="json", exclude_unset=True,
    )
    repo.save("caller-uid", changes)
    db.collection.assert_called_once_with("user_profiles")
    db.collection.return_value.document.assert_called_once_with("caller-uid")
    saved = db.collection.return_value.document.return_value.set
    assert saved.call_args.kwargs == {"merge": True}
    data = saved.call_args.args[0]
    assert data["date_of_birth"] == "2000-02-29"
    assert data["blood_group"] == "O+"
    assert "gender" not in data
