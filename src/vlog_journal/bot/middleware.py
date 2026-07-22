from typing import Any
import structlog
from telebot.asyncio_handler_backends import BaseMiddleware, CancelUpdate
from telebot.types import Message

logger = structlog.get_logger(__name__)

class WhitelistMiddleware(BaseMiddleware):
    """Middleware that restricts bot access to whitelisted Telegram user IDs."""

    def __init__(self, allowed_user_ids: set[int] | list[int] | str) -> None:
        super().__init__()
        self.update_types = ["message"]
        if isinstance(allowed_user_ids, str):
            self.allowed_user_ids: set[int] = {
                int(uid.strip())
                for uid in allowed_user_ids.split(",")
                if uid.strip().isdigit()
            }
        else:
            self.allowed_user_ids = set(allowed_user_ids)

    async def pre_process(self, message: Message, data: dict[str, Any]) -> CancelUpdate | None:
        if not message.from_user:
            logger.warning("Message missing from_user, dropping update")
            return CancelUpdate()

        user_id = message.from_user.id
        if user_id not in self.allowed_user_ids:
            logger.warning(
                "Unauthorized access attempt ignored",
                user_id=user_id,
                username=getattr(message.from_user, "username", None),
                chat_id=message.chat.id,
            )
            return CancelUpdate()

        return None

    async def post_process(self, message: Message, data: dict[str, Any], exception: Exception | None) -> None:
        pass
