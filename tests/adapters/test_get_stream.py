"""Tests for GetStreamAdapter."""

from event_booking.adapters.get_stream import GetStreamAdapter


class TestEncodeUserId:
    def test_deterministic(self) -> None:
        adapter = GetStreamAdapter(
            chat_api_key="key",
            chat_api_secret="secret",
            user_id_encryption_key="test-encryption-key",
        )
        result1 = adapter._encode_user_id(user_id="user@test.com")
        result2 = adapter._encode_user_id(user_id="user@test.com")
        assert result1 == result2

    def test_different_inputs_different_outputs(self) -> None:
        adapter = GetStreamAdapter(
            chat_api_key="key",
            chat_api_secret="secret",
            user_id_encryption_key="test-encryption-key",
        )
        result1 = adapter._encode_user_id(user_id="user1@test.com")
        result2 = adapter._encode_user_id(user_id="user2@test.com")
        assert result1 != result2
