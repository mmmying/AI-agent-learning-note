import json
from pathlib import Path
from uuid import uuid4

from models.schemas import Message


class MemoryStore:
    def __init__(self, path: Path = Path("data/conversations.json")) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def create_conversation_id(self) -> str:
        return str(uuid4())

    def load_all(self) -> dict[str, list[dict]]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save_all(self, data: dict[str, list[dict]]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 从所有对话数据中获取指定对话的对话消息
    def get_messages(self, conversation_id: str) -> list[Message]:
        data = self.load_all()
        return [Message(**item) for item in data.get(conversation_id, [])]

    # 向指定对话中添加对话消息
    def add_message(self, conversation_id: str, message: Message) -> None:
        data = self.load_all()
        data.setdefault(conversation_id, [])
        data[conversation_id].append(message.model_dump())
        self.save_all(data)
