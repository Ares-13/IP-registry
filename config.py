import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

ENV_PATH = BASE_DIR / ".env"


def load_env() -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text(
            "DB_HOST=\n"
            "DB_PORT=5432\n"
            "DB_NAME=\n"
            "DB_USER=\n"
            "DB_PASSWORD=\n"
            "OPERATOR=admin\n",
            encoding="utf-8",
        )
        raise FileNotFoundError(
            "Файл .env не найден. Рядом с программой создан шаблон — заполните его и запустите снова."
        )

    load_dotenv(dotenv_path=ENV_PATH, encoding="utf-8")

    required = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(
            "В файле .env не заполнены параметры: " + ", ".join(missing)
        )


def get_db_config() -> dict:
    load_env()
    return {
        "host": os.getenv("DB_HOST"),
        "port": os.getenv("DB_PORT"),
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
    }


def get_default_operator() -> str:
    load_env()
    return os.getenv("OPERATOR") or "admin"
