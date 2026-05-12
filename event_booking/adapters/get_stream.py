"""GetStream Chat SDK wrapper with AES user ID encryption."""

import base64
import hashlib

import structlog
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from stream_chat import StreamChat

logger = structlog.get_logger(__name__)


class GetStreamAdapter:
    def __init__(self, chat_api_key: str, chat_api_secret: str, user_id_encryption_key: str) -> None:
        self._client = StreamChat(api_key=chat_api_key, api_secret=chat_api_secret)
        self._cipher_key = hashlib.sha256(user_id_encryption_key.encode()).digest()[:16]
        self._iv = b"\x00" * 16

    def _encode_user_id(self, *, user_id: str) -> str:
        cipher = Cipher(algorithms.AES128(self._cipher_key), modes.CBC(self._iv))
        encryptor = cipher.encryptor()
        padder = PKCS7(128).padder()
        padded = padder.update(user_id.encode()) + padder.finalize()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return base64.urlsafe_b64encode(encrypted).decode().rstrip("=")

    async def create_chat(self, *, channel_id: str, organizer_id: str, client_id: str) -> None:
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
        logger.info("Chat created", channel_id=channel_id)

    async def delete_chat(self, *, channel_id: str) -> None:
        channel = self._client.channel("messaging", channel_id)
        channel.delete()
        logger.info("Chat deleted", channel_id=channel_id)

    async def send_message(self, *, channel_id: str, user_id: str, message: dict) -> None:
        encoded = self._encode_user_id(user_id=user_id)
        channel = self._client.channel("messaging", channel_id)
        channel.send_message(message, encoded)

    def create_token(self, *, user_id: str, name: str, expires_at: int) -> str:
        encoded = self._encode_user_id(user_id=user_id)
        self._client.upsert_users([{"id": encoded, "name": name}])
        return self._client.create_token(encoded, expiration=expires_at)
