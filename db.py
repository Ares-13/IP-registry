from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import os
from typing import Any, Iterator, Optional, Union

import psycopg2
import psycopg2.extras

from config import get_db_config

# libpq на русской Windows иначе падает с UnicodeDecodeError при ошибках подключения.
os.environ["PGCLIENTENCODING"] = "UTF8"
os.environ["LC_MESSAGES"] = "English"


def _connect():
    cfg = get_db_config()
    return psycopg2.connect(**cfg, client_encoding="UTF8")


@contextmanager
def get_connection() -> Iterator[Any]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_ws(row: dict) -> dict:
    return {
        "id": row["id"],
        "ip_address": str(row["ip_address"]),
        "full_name": row["full_name"],
        "room_number": row["room_number"],
        "ticket_id": row["ticket_id"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"] or "",
    }


def _row_to_history(row: dict) -> dict:
    return {
        "history_id": row["history_id"],
        "id_original": row["id_original"],
        "ip_address": str(row["ip_address"]),
        "full_name": row["full_name"],
        "room_number": row["room_number"],
        "ticket_id": row["ticket_id"] or "",
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "operation": row["operation"],
        "changed_by": row["changed_by"] or "",
    }


def fetch_workstations(
    search: str = "",
    full_name: str = "",
    room_number: str = "",
) -> list[dict]:
    query = """
        SELECT id, ip_address, full_name, room_number, ticket_id,
               created_at, updated_at, updated_by
        FROM workstations
        WHERE (
            %(search)s = ''
            OR ip_address::text ILIKE %(like)s
            OR full_name ILIKE %(like)s
            OR room_number ILIKE %(like)s
            OR COALESCE(ticket_id, '') ILIKE %(like)s
        )
        AND (
            %(full_name)s = ''
            OR full_name ILIKE %(name_like)s
        )
        AND (
            %(room_number)s = ''
            OR room_number ILIKE %(room_like)s
        )
        ORDER BY room_number, ip_address
    """
    search = search.strip()
    full_name = full_name.strip()
    room_number = room_number.strip()
    params = {
        "search": search,
        "like": f"%{search}%",
        "full_name": full_name,
        "name_like": f"%{full_name}%",
        "room_number": room_number,
        "room_like": f"%{room_number}%",
    }
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [_row_to_ws(row) for row in cur.fetchall()]


def fetch_history(
    search: str = "",
    full_name: str = "",
    room_number: str = "",
) -> list[dict]:
    query = """
        SELECT history_id, id_original, ip_address, full_name, room_number,
               ticket_id, valid_from, valid_to, operation, changed_by
        FROM workstations_history
        WHERE (
            %(search)s = ''
            OR ip_address::text ILIKE %(like)s
            OR full_name ILIKE %(like)s
            OR room_number ILIKE %(like)s
            OR COALESCE(ticket_id, '') ILIKE %(like)s
            OR operation ILIKE %(like)s
        )
        AND (
            %(full_name)s = ''
            OR full_name ILIKE %(name_like)s
        )
        AND (
            %(room_number)s = ''
            OR room_number ILIKE %(room_like)s
        )
        ORDER BY valid_to DESC, history_id DESC
    """
    search = search.strip()
    full_name = full_name.strip()
    room_number = room_number.strip()
    params = {
        "search": search,
        "like": f"%{search}%",
        "full_name": full_name,
        "name_like": f"%{full_name}%",
        "room_number": room_number,
        "room_like": f"%{room_number}%",
    }
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [_row_to_history(row) for row in cur.fetchall()]


def export_workstations_txt(output_path: Union[str, Path]) -> int:
    """Выгружает актуальные рабочие станции в .txt — тот же формат, что у CLI."""
    output_path = Path(output_path)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM workstations ORDER BY room_number;")
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"Отчёт сформирован: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n")
        f.write("\t".join(col_names) + "\n")
        f.write("-" * 80 + "\n")
        for row in rows:
            line = "\t".join("" if value is None else str(value) for value in row)
            f.write(line + "\n")
    return len(rows)


def get_workstation(ws_id: int) -> Optional[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, ip_address, full_name, room_number, ticket_id,
                       created_at, updated_at, updated_by
                FROM workstations
                WHERE id = %s
                """,
                (ws_id,),
            )
            row = cur.fetchone()
            return _row_to_ws(row) if row else None


def add_workstation(
    ip_address: str,
    full_name: str,
    room_number: str,
    ticket_id: Optional[str],
    updated_by: str,
) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO workstations (
                    ip_address, full_name, room_number, ticket_id, updated_by
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (ip_address, full_name, room_number, ticket_id or None, updated_by),
            )
            return cur.fetchone()[0]


def _insert_history(cur, row: dict, operation: str, changed_by: str, valid_to: datetime) -> None:
    cur.execute(
        """
        INSERT INTO workstations_history (
            id_original, ip_address, full_name, room_number, ticket_id,
            valid_from, valid_to, operation, changed_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row["id"],
            row["ip_address"],
            row["full_name"],
            row["room_number"],
            row["ticket_id"] or None,
            row["updated_at"],
            valid_to,
            operation,
            changed_by,
        ),
    )


def update_workstation(
    ws_id: int,
    ip_address: str,
    full_name: str,
    room_number: str,
    ticket_id: Optional[str],
    updated_by: str,
) -> None:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, ip_address, full_name, room_number, ticket_id,
                       created_at, updated_at, updated_by
                FROM workstations
                WHERE id = %s
                FOR UPDATE
                """,
                (ws_id,),
            )
            current = cur.fetchone()
            if not current:
                raise ValueError("Запись не найдена — возможно, её уже отправили в архив.")

            old = _row_to_ws(current)
            now = datetime.now(old["updated_at"].tzinfo) if old["updated_at"] else datetime.now()
            #_insert_history(cur, old, "UPDATE", updated_by, now)

            cur.execute(
                """
                UPDATE workstations
                SET ip_address = %s,
                    full_name = %s,
                    room_number = %s,
                    ticket_id = %s,
                    updated_at = %s,
                    updated_by = %s
                WHERE id = %s
                """,
                (
                    ip_address,
                    full_name,
                    room_number,
                    ticket_id or None,
                    now,
                    updated_by,
                    ws_id,
                ),
            )


def archive_workstation(ws_id: int, changed_by: str) -> None:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, ip_address, full_name, room_number, ticket_id,
                       created_at, updated_at, updated_by
                FROM workstations
                WHERE id = %s
                FOR UPDATE
                """,
                (ws_id,),
            )
            current = cur.fetchone()
            if not current:
                raise ValueError("Запись не найдена.")

            old = _row_to_ws(current)
            now = datetime.now(old["updated_at"].tzinfo) if old["updated_at"] else datetime.now()
            #_insert_history(cur, old, "DELETE", changed_by, now)
            cur.execute("DELETE FROM workstations WHERE id = %s", (ws_id,))


def restore_from_history(history_id: int, updated_by: str) -> int:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT history_id, id_original, ip_address, full_name, room_number,
                       ticket_id, valid_from, valid_to, operation, changed_by
                FROM workstations_history
                WHERE history_id = %s
                """,
                (history_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Запись архива не найдена.")

            hist = _row_to_history(row)
            cur.execute(
                """
                INSERT INTO workstations (
                    ip_address, full_name, room_number, ticket_id, updated_by
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    hist["ip_address"],
                    hist["full_name"],
                    hist["room_number"],
                    hist["ticket_id"] or None,
                    updated_by,
                ),
            )
            return cur.fetchone()["id"]
