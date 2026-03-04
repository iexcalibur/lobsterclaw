from __future__ import annotations

from agent.pairing import PairingStore


def test_pairing_code_redeem_approves_user_once(tmp_path):
    store = PairingStore(tmp_path / "pairing.db")
    code = store.create_pairing_code(created_by=1, ttl_minutes=15, max_uses=1, length=6)

    ok, status = store.redeem_code(
        code=code,
        user_id=1001,
        username="alice",
        first_name="Alice",
    )
    assert ok is True
    assert status == "approved"
    assert store.is_approved(1001) is True

    ok2, status2 = store.redeem_code(
        code=code,
        user_id=1002,
        username="bob",
        first_name="Bob",
    )
    assert ok2 is False
    assert status2 == "used"


def test_pairing_code_immediate_expiry(tmp_path):
    store = PairingStore(tmp_path / "pairing.db")
    code = store.create_pairing_code(created_by=1, ttl_minutes=0, max_uses=1, length=6)

    ok, status = store.redeem_code(
        code=code,
        user_id=1003,
        username="eve",
        first_name="Eve",
    )
    assert ok is False
    assert status == "expired"


def test_list_active_codes_excludes_expired_and_used(tmp_path):
    store = PairingStore(tmp_path / "pairing.db")
    fresh = store.create_pairing_code(created_by=1, ttl_minutes=15, max_uses=1, length=6)
    used = store.create_pairing_code(created_by=1, ttl_minutes=15, max_uses=1, length=6)
    expired = store.create_pairing_code(created_by=1, ttl_minutes=0, max_uses=1, length=6)

    # Consume one code
    ok, status = store.redeem_code(
        code=used,
        user_id=2001,
        username="used_user",
        first_name="Used",
    )
    assert ok is True
    assert status == "approved"

    # Mark one code expired by attempting redemption
    ok2, status2 = store.redeem_code(
        code=expired,
        user_id=2002,
        username="expired_user",
        first_name="Expired",
    )
    assert ok2 is False
    assert status2 == "expired"

    active_codes = {row["code"] for row in store.list_active_codes()}
    assert fresh in active_codes
    assert used not in active_codes
    assert expired not in active_codes
