# 入口层：协调程序执行过程，只负责告诉系统下一步做什么
from client import AIClient
from logger import setup_logging


def main() -> None:
    setup_logging()

    client = AIClient()

    result = client.send("hello")

    print(result)


if __name__ == "__main__":
    main()
