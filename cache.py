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


def push_audit_event(operation: str, ip: str, operator: str, details: str) -> None:
    """Отправляет событие в потоковую очередь Redis Streams."""
    try:
        event_data = {
            "operation": operation,
            "ip_address": ip,
            "operator": operator,
            "details": details
        }
        # Команда xadd добавляет запись в поток с именем 'audit_stream'.
        # ID="*" означает, что Redis сам сгенерирует уникальный номер на основе текущего времени.
        client.xadd("audit_stream", event_data, id="*")
    except redis.ConnectionError:
        pass