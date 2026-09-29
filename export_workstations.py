import sys
import os
import logging
import psycopg2
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Определяем путь
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

env_path = BASE_DIR / ".env"

# Если файла .env нет, создаем пустой шаблон и просим заполнить
if not env_path.exists():
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("DB_HOST=\n")
        f.write("DB_PORT=5432\n")
        f.write("DB_NAME=\n")
        f.write("DB_USER=\n")
        f.write("DB_PASSWORD=\n")
    
    logger.warning("Файл .env не найден! Рядом с программой создан пустой шаблон .env.")
    logger.warning("Пожалуйста, откройте его в блокноте, впишите данные для подключения к БД и запустите программу снова.")
    input("\nНажмите Enter для выхода...")
    sys.exit(1)

# Если файл есть, загружаем его
load_dotenv(dotenv_path=env_path)

# Проверяем, что все обязательные переменные заполнены (не пустые)
REQUIRED_ENV_VARS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]

if missing_vars:
    logger.error("В файле .env не заполнены следующие параметры: %s", ", ".join(missing_vars))
    logger.error("Заполните их и запустите программу снова.")
    input("\nНажмите Enter для выхода...")
    sys.exit(1)

# Подтягиваем данные строго из окружения
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

# Создаем папку ip_registry рядом с исполняемым файлом
OUTPUT_DIR = BASE_DIR / "ip_registry"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Формируем имя итогового файла
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
OUTPUT_FILE = OUTPUT_DIR / f"workstations_report_{timestamp}.txt"


def export_to_txt(output_path: Path = OUTPUT_FILE) -> int:
    """Выгружает все строки из workstations в текстовый файл."""
    conn = None
    row_count = 0

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("SELECT * FROM workstations ORDER BY room_number;")
        rows = cur.fetchall()
        col_names = [desc[0] for desc in cur.description]

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"Отчёт сформирован: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 90 + "\n")
            f.write("\t".join(col_names) + "\n")
            f.write("-" * 90 + "\n")

            for row in rows:
                line = "\t".join(str(value) if value is not None else "" for value in row)
                f.write(line + "\n")
                row_count += 1

        logger.info("Успех! Экспортировано %d строк в файл %s", row_count, output_path)

    except psycopg2.Error as e:
        logger.error("Ошибка при работе с БД: %s", e)
    except OSError as e:
        logger.error("Ошибка при записи файла %s: %s", output_path, e)
    finally: 
        if conn:
            conn.close()

    return row_count


if __name__ == "__main__":
    export_to_txt()
    # Пауза в конце, чтобы сисадмин увидел, что отчет сформирован, а окно не закрылось мгновенно
    input("\nРабота завершена. Нажмите Enter для выхода...")