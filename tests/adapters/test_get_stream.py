"""Tests for GetStreamAdapter."""

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from stream_chat.base.exceptions import StreamAPIException

from event_booking.adapters.get_stream import GetStreamAdapter

ENCRYPTION_KEY = "test-encryption-key"


def _receiver_style_decode(encoded_user_id: str, secret: str) -> str:
    """Reimplementation of event-receiver's decode_getstream_user_id GCM path."""
    key = hashlib.sha256(secret.encode()).digest()
    padding_needed = len(encoded_user_id) % 4
    if padding_needed:
        encoded_user_id += "=" * (4 - padding_needed)
    blob = base64.urlsafe_b64decode(encoded_user_id)
    nonce, ciphertext = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode()


@pytest.fixture
def adapter() -> GetStreamAdapter:
    with patch("event_booking.adapters.get_stream.StreamChat"):
        return GetStreamAdapter(
            chat_api_key="key",
            chat_api_secret="secret",
            user_id_encryption_key=ENCRYPTION_KEY,
        )


class TestEncodeUserId:
    def test_deterministic(self, adapter: GetStreamAdapter) -> None:
        result1 = adapter._encode_user_id(user_id="user@test.com")
        result2 = adapter._encode_user_id(user_id="user@test.com")
        assert result1 == result2

    def test_different_inputs_different_outputs(self, adapter: GetStreamAdapter) -> None:
        result1 = adapter._encode_user_id(user_id="user1@test.com")
        result2 = adapter._encode_user_id(user_id="user2@test.com")
        assert result1 != result2

    def test_decodable_by_event_receiver_gcm_format(self, adapter: GetStreamAdapter) -> None:
        encoded = adapter._encode_user_id(user_id="user@test.com")
        assert _receiver_style_decode(encoded, ENCRYPTION_KEY) == "user@test.com"

    def test_authenticated_encryption_rejects_tampering(self, adapter: GetStreamAdapter) -> None:
        encoded = adapter._encode_user_id(user_id="user@test.com")
        blob = bytearray(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        blob[-1] ^= 0x01
        tampered = base64.urlsafe_b64encode(bytes(blob)).rstrip(b"=").decode()
        with pytest.raises(InvalidTag):
            _receiver_style_decode(tampered, ENCRYPTION_KEY)


class TestCreateToken:
    async def test_uses_exp_kwarg(self, adapter: GetStreamAdapter) -> None:
        adapter._client = MagicMock()
        adapter._client.create_token.return_value = "jwt"

        token = await adapter.create_token(user_id="user@test.com", name="User", expires_at=1750000000)

        assert token == "jwt"
        _, kwargs = adapter._client.create_token.call_args
        assert kwargs == {"exp": 1750000000}
        adapter._client.upsert_users.assert_called_once()


class TestDeleteChat:
    async def test_passes_hard_flag(self, adapter: GetStreamAdapter) -> None:
        channel = MagicMock()
        adapter._client = MagicMock()
        adapter._client.channel.return_value = channel

        await adapter.delete_chat(channel_id="chan-1", hard=True)

        channel.delete.assert_called_once_with(hard=True)

    async def test_missing_channel_is_success(self, adapter: GetStreamAdapter) -> None:
        channel = MagicMock()
        channel.delete.side_effect = StreamAPIException('{"code": 16, "message": "not found"}', 404)
        adapter._client = MagicMock()
        adapter._client.channel.return_value = channel

        await adapter.delete_chat(channel_id="chan-1")  # must not raise

    async def test_other_errors_propagate(self, adapter: GetStreamAdapter) -> None:
        channel = MagicMock()
        channel.delete.side_effect = StreamAPIException('{"code": 99, "message": "boom"}', 500)
        adapter._client = MagicMock()
        adapter._client.channel.return_value = channel

        with pytest.raises(StreamAPIException):
            await adapter.delete_chat(channel_id="chan-1")


class TestHasMessages:
    async def test_true_when_messages_exist(self, adapter: GetStreamAdapter) -> None:
        channel = MagicMock()
        channel.query.return_value = {"messages": [{"id": "m1"}]}
        adapter._client = MagicMock()
        adapter._client.channel.return_value = channel

        assert await adapter.has_messages(channel_id="chan-1") is True

    async def test_false_when_empty(self, adapter: GetStreamAdapter) -> None:
        channel = MagicMock()
        channel.query.return_value = {"messages": []}
        adapter._client = MagicMock()
        adapter._client.channel.return_value = channel

        assert await adapter.has_messages(channel_id="chan-1") is False


class TestEventLoopOffload:
    async def test_create_chat_runs_sdk_in_thread(self, adapter: GetStreamAdapter) -> None:
        adapter._client = MagicMock()

        with patch("event_booking.adapters.get_stream.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            await adapter.create_chat(channel_id="c", organizer_id="o@t.com", client_id="cl@t.com")

        mock_to_thread.assert_called_once()
