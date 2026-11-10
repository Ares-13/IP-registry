import json
import redis

client = redis.Redis(host='localhost', port=6379, decode_responses=True)

def get_cached_user(username: str) -> dict | None:
    """Пытается найти данные пользователя в Redis."""
    try:
        data = client.get(f"user:{username}")
        if data:
            return json.loads(data)
    except redis.ConnectionError:
        pass

    return None


def set_cached_user(username: str, user_data: dict, expire_seconds: int = 28800) -> None:
    """Сохраняет данные пользователя в Redis на 8 часов (28800 секунд)."""
    try:
        client.set(f"user:{username}", json.dumps(user_data), ex=expire_seconds)
    except redis.ConnectionError:
        pass


def push_audit_event(
    operation: str, 
    ip: str, 
    operator: str, 
    details: str, 
    ws_id: int, 
    full_name: str, 
    room_number: str, 
    ticket_id: str = "", 
    note: str = ""
) -> None:
    """Отправляет событие со всеми атрибутами в очередь Redis Streams."""
    try:
        event_data = {
            "id_original": str(ws_id),
            "ip_address": ip,
            "full_name": full_name,
            "room_number": room_number,
            "ticket_id": ticket_id if ticket_id else "",
            "note": note if note else "",
            "operation": operation,
            "changed_by": operator,       # Переименовано для совпадения с Airflow
            "change_details": details     # Переименовано для совпадения с Airflow
        }
        # ID="*" означает, что Redis сам сгенерирует уникальный номер (timestamp)
        client.xadd("audit_stream", event_data, id="*")
    except redis.ConnectionError:
        pass