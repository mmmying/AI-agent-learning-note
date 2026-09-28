from pathlib import Path

# 项目根目录（ai-chat-backend/），以本文件位置推导，
# 这样无论从哪个工作目录启动服务，下面的路径都不会失效
BASE_DIR = Path(__file__).resolve().parent.parent

# .env 配置文件
ENV_FILE = BASE_DIR / ".env"

# 运行时数据目录与 SQLite 数据库文件
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "chat.db"

# 提示词目录与系统提示词文件
PROMPTS_DIR = BASE_DIR / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system.md"
