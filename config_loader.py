import json

def load_config():
    """Загружает настройки из config.json."""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка чтения config.json: {e}")
        return None
