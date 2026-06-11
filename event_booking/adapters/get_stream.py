"""GetStream Chat SDK wrapper with AES-GCM user ID encryption.

User-id format (MUST stay compatible with event-receiver's
``decode_getstream_user_id``): ``urlsafe_b64(nonce[12] + ciphertext + tag[16])``
without padding, key = ``sha256(secret)`` (32 bytes). The nonce is derived
deterministically via HMAC-SHA256 over the plaintext so the same email always
maps to the same GetStream user id (SIV-style; required for stable channel
membership and tokens), while remaining decryptable by the standard GCM path.

The official SDK is synchronous; every network call is offloaded with
``asyncio.to_thread`` so the event loop is never blocked.
"""

import asyncio
import base64
import hashlib
import hmac
from http import HTTPStatus

import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from stream_chat import StreamChat
from stream_chat.base.exceptions import StreamAPIException

logger = structlog.get_logger(__name__)

_GCM_NONCE_LENGTH = 12
_NONCE_CONTEXT = b"getstream-user-id:"


class GetStreamAdapter:
    def __init__(
        self,
        chat_api_key: str,
        chat_api_secret: str,
        user_id_encryption_key: str,
        timeout_seconds: float = 6.0,
    ) -> None:
        self._client = StreamChat(api_key=chat_api_key, api_secret=chat_api_secret, timeout=timeout_seconds)
        self._cipher_key = hashlib.sha256(user_id_encryption_key.encode()).digest()

    def _encode_user_id(self, *, user_id: str) -> str:
        plaintext = user_id.encode()
        nonce = hmac.new(self._cipher_key, _NONCE_CONTEXT + plaintext, hashlib.sha256).digest()[:_GCM_NONCE_LENGTH]
        ciphertext = AESGCM(self._cipher_key).encrypt(nonce, plaintext, None)
        return base64.urlsafe_b64encode(nonce + ciphertext).rstrip(b"=").decode()

    async def create_chat(self, *, channel_id: str, organizer_id: str, client_id: str) -> None:
        """Create the channel; idempotent — GetStream returns the existing channel for the same id."""
        await asyncio.to_thread(self._create_chat_sync, channel_id, organizer_id, client_id)
        logger.info("Chat ensured", channel_id=channel_id)

    def _create_chat_sync(self, channel_id: str, organizer_id: str, client_id: str) -> None:
        encoded_organizer = self._encode_user_id(user_id=organizer_id)
        encoded_client = self._encode_user_id(user_id=client_id)
        self._client.upsert_users(
            [
                {"id": encoded_organizer, "name": organizer_id},
                {"id": encoded_client, "name": client_id},
            ]
        )
        channel = self._client.channel("messaging", channel_id)
        channel.create(encoded_organizer, members=[encoded_organizer, encoded_client])

    async def has_messages(self, *, channel_id: str) -> bool:
        """True when the channel already contains messages (used to skip duplicate welcomes)."""
        return await asyncio.to_thread(self._has_messages_sync, channel_id)

    def _has_messages_sync(self, channel_id: str) -> bool:
        channel = self._client.channel("messaging", channel_id)
        response = channel.query(messages={"limit": 1})
        return bool(response.get("messages"))

    async def delete_chat(self, *, channel_id: str, hard: bool = False) -> None:
        """Delete the channel; a missing channel is treated as success (idempotent redelivery)."""
        try:
            await asyncio.to_thread(self._delete_chat_sync, channel_id, hard)
        except StreamAPIException as exc:
            if exc.status_code == HTTPStatus.NOT_FOUND:
                logger.info("Chat already absent", channel_id=channel_id)
                return
            raise
        logger.info("Chat deleted", channel_id=channel_id, hard=hard)

    def _delete_chat_sync(self, channel_id: str, hard: bool) -> None:
        channel = self._client.channel("messaging", channel_id)
        channel.delete(hard=hard)

    async def send_message(self, *, channel_id: str, user_id: str, message: dict) -> None:
        await asyncio.to_thread(self._send_message_sync, channel_id, user_id, message)

    def _send_message_sync(self, channel_id: str, user_id: str, message: dict) -> None:
        encoded = self._encode_user_id(user_id=user_id)
        channel = self._client.channel("messaging", channel_id)
        channel.send_message(message, encoded)

    async def create_token(self, *, user_id: str, name: str, expires_at: int) -> str:
        """Mint a chat JWT that actually expires (SDK kwarg is ``exp``, not ``expiration``)."""
        return await asyncio.to_thread(self._create_token_sync, user_id, name, expires_at)

    def _create_token_sync(self, user_id: str, name: str, expires_at: int) -> str:
        encoded = self._encode_user_id(user_id=user_id)
        self._client.upsert_users([{"id": encoded, "name": name}])
        return self._client.create_token(encoded, exp=expires_at)
