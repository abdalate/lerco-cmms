"""Administrator authentication and Approved headcount access controls."""
import pytest
import database as db


def test_admin_bootstrap_login_and_approved_audit():
    db.create_admin_account("admin", "CorrectHorseBattery12")
    assert db.admin_login("admin", "wrong-password") is None
    token = db.admin_login("admin", "CorrectHorseBattery12")
    assert token
    assert db.admin_session_valid(token)

    db.set_department_approved("M", 12, token)
    assert db.get_department_approved("M") == 12
    audit = db.admin_audit_log()
    assert audit[0]["action"] == "APPROVED_HEADCOUNT_CHANGED"
    assert audit[0]["old_value"] == "0"
    assert audit[0]["new_value"] == "12"
    db.admin_logout(token)
    assert not db.admin_session_valid(token)
    with pytest.raises(PermissionError):
        db.set_department_approved("M", 15, token)


def test_department_daily_entry_cannot_change_approved():
    token = db.admin_login("admin", "CorrectHorseBattery12")
    assert token
    db.set_department_approved("M", 9, token)
    db.save_manpower(__import__("datetime").date.today(), "M", approved=999, technicians=4)
    saved = db.get_manpower(__import__("datetime").date.today(), "M")
    assert saved["approved"] == 9
    assert saved["technicians"] == 4


def test_expired_admin_session_is_rejected():
    import hashlib
    token = db.admin_login("admin", "CorrectHorseBattery12")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with db.tx() as conn:
        conn.execute("UPDATE admin_sessions SET expires_at='2000-01-01 00:00:00' WHERE token_hash=?",
                     (digest,))
    assert not db.admin_session_valid(token)
    with pytest.raises(PermissionError):
        db.set_department_approved("E", 5, token)
