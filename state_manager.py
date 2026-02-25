import json
import threading
from datetime import datetime
from pytz import timezone as pytz_timezone

import config_loader

# --- ГЛОБАЛЬНОЕ СОСТОЯНИЕ ОЧЕРЕДИ ---
STATE_FILE = "queue_state.json"
# Блокировка для потокобезопасной записи
queue_lock = threading.Lock()

current_session = {
    "active": False,
    "message_id": None,
    "chat_id": None,
    "start_time": None,
    "queues": {},
    "config": {}
}

# --- СЕРИАЛИЗАЦИЯ ВРЕМЕНИ ДЛЯ JSON ---
def dt_to_str(dt_obj):
    """Преобразование datetime в строку ISO для сохранения."""
    return dt_obj.isoformat() if dt_obj else None

def str_to_dt(dt_str, tz_name="UTC"):
    """Преобразование строки в datetime с учетом часового пояса."""
    if not dt_str: return None
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.astimezone(pytz_timezone(tz_name))
    except ValueError:
        return None

# --- ПОСТОЯННОЕ ХРАНЕНИЕ ---
def save_state():
    """Сохраняет текущее состояние очереди в JSON файл."""
    with queue_lock:
        data_to_save = current_session.copy()
        data_to_save['start_time'] = dt_to_str(data_to_save['start_time'])

        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Состояние очереди сохранено в {STATE_FILE}")
        except Exception as e:
            print(f"Ошибка при сохранении состояния: {e}")

def load_state():
    """Загружает состояние очереди из JSON файла при запуске."""
    global current_session
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)

        tz_name = loaded_data.get('config', {}).get('timezone', 'UTC')
        loaded_data['start_time'] = str_to_dt(loaded_data.get('start_time'), tz_name)

        current_session.update(loaded_data)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Состояние очереди успешно загружено.")

        return True
    except FileNotFoundError:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] Файл состояния {STATE_FILE} не найден. Начинаем с чистого листа.")
        current_session["config"] = config_loader.load_config() or {}
        return False
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Ошибка при загрузке состояния: {e}")
        return False

def get_current_session():
    """Возвращает текущий объект сессии. Является чистокровным костылём."""
    return current_session
