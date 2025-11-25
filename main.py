import os
import json
import time
import threading
from datetime import datetime, timedelta
import telebot
from telebot import types
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone as pytz_timezone

# --- КОНСТАНТЫ И ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("Ошибка: BOT_TOKEN не найден в файле .env")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# Файл для сохранения состояния очереди
STATE_FILE = "queue_state.json"

# --- ГЛОБАЛЬНОЕ СОСТОЯНИЕ ОЧЕРЕДИ ---
# start_time: datetime object in memory, string in JSON
current_session = {
    "active": False,
    "message_id": None,
    "chat_id": None,
    "start_time": None,
    "queues": {},
    "config": {}
}

# Блокировка для потокобезопасной записи
queue_lock = threading.Lock()


# --- ФУНКЦИИ КОНФИГУРАЦИИ ---

def load_config():
    """Загружает настройки из config.json."""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка чтения config.json: {e}")
        return None


# --- ФУНКЦИИ СЕРИАЛИЗАЦИИ ВРЕМЕНИ ДЛЯ JSON ---

def dt_to_str(dt_obj):
    """Преобразование datetime в строку ISO для сохранения."""
    return dt_obj.isoformat() if dt_obj else None


def str_to_dt(dt_str, tz_name="UTC"):
    """Преобразование строки в datetime с учетом часового пояса."""
    if not dt_str: return None
    try:
        dt = datetime.fromisoformat(dt_str)
        # Убедимся, что время в правильном часовом поясе
        return dt.astimezone(pytz_timezone(tz_name))
    except ValueError:
        return None


# --- ФУНКЦИИ ПОСТОЯННОГО ХРАНЕНИЯ ---

def save_state():
    """Сохраняет текущее состояние очереди в JSON файл."""
    with queue_lock:
        data_to_save = current_session.copy()
        # Преобразуем datetime в строку перед сохранением
        data_to_save['start_time'] = dt_to_str(data_to_save['start_time'])

        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Состояние очереди сохранено в {STATE_FILE}")
        except Exception as e:
            print(f"Ошибка при сохранении состояния: {e}")


def force_update_and_save():
    """Принудительное обновление сообщения и сохранение состояния (срабатывает таймером)."""
    update_message_ui(force_save=True)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Период задержки прошел. Сообщение обновлено и состояние сохранено.")


def load_state():
    """Загружает состояние очереди из JSON файла при запуске."""
    global current_session
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)

        # Загружаем время, преобразуя строку обратно в datetime
        tz_name = loaded_data['config'].get('timezone', 'UTC')
        loaded_data['start_time'] = str_to_dt(loaded_data.get('start_time'), tz_name)

        # Обновляем глобальное состояние
        current_session.update(loaded_data)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Состояние очереди успешно загружено.")

        # --- Восстановление таймера задержки, если сессия активна ---
        if current_session["active"] and current_session["message_id"]:
            print("Бот возобновляет активную сессию.")

            tz = current_session["config"].get("timezone", "UTC")
            start_time = current_session["start_time"]
            delay_minutes = current_session["config"].get("delay", 0)  # Используем "delay" из конфига

            if start_time and delay_minutes > 0:
                now = datetime.now(pytz_timezone(tz))
                time_passed_seconds = (now - start_time).total_seconds()
                remaining_time_seconds = (delay_minutes * 60) - time_passed_seconds

                if remaining_time_seconds > 5:  # Если осталось больше 5 секунд, перезапускаем таймер
                    timer = threading.Timer(remaining_time_seconds, force_update_and_save)
                    timer.start()
                    print(f"Запланировано первое обновление/сохранение через {int(remaining_time_seconds)} секунд.")
                else:
                    # Если время почти вышло или уже вышло, обновляем сразу
                    force_update_and_save()

        return True
    except FileNotFoundError:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] Файл состояния {STATE_FILE} не найден. Начинаем с чистого листа.")
        return False
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Ошибка при загрузке состояния: {e}")
        return False


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СООБЩЕНИЯ ---

def get_user_display_name(user):
    """Формирует строку: Имя Фамилия (@username)"""
    first = user.first_name or ""
    last = user.last_name or ""
    username = f" (@{user.username})" if user.username else ""
    return f"{first} {last}{username}".strip()


def generate_message_text(queues_data):
    """Генерирует текст сообщения со списками"""
    text = "📅 **Запись к преподавателям**\n\n"

    for teacher, users in queues_data.items():
        text += f"🎓 **{teacher}**:\n"
        if not users:
            text += "_Очередь пуста_\n"
        else:
            for idx, user_data in enumerate(users, 1):
                text += f"{idx}. {user_data['display_name']}\n"
        text += "\n"

    return text


def generate_keyboard(teachers):
    """Создает кнопки с именами преподавателей"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for teacher in teachers:
        markup.add(types.InlineKeyboardButton(text=teacher, callback_data=f"join_{teacher}"))
    return markup


# --- ФУНКЦИИ ОЧЕРЕДИ ---

def send_weekly_message(chat_id, topic_id, teachers, tz, delay_minutes):
    """Отправляет новое сообщение с очередью по расписанию."""
    global current_session

    # Инициализируем сессию для нового запланированного сообщения
    with queue_lock:
        current_session["queues"] = {t: [] for t in teachers}
        current_session["active"] = True
        current_session["start_time"] = datetime.now(pytz_timezone(tz))
        current_session["chat_id"] = chat_id
        # Сохраняем delay_minutes в конфиге сессии
        current_session["config"] = {"teachers": teachers, "timezone": tz, "delay": delay_minutes}

    text = generate_message_text(current_session["queues"])
    keyboard = generate_keyboard(teachers)

    try:
        # Пытаемся отправить новое сообщение
        msg = bot.send_message(
            chat_id,
            text,
            message_thread_id=topic_id,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        current_session["message_id"] = msg.message_id
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Новая очередь открыта. ID сообщения: {msg.message_id}")

        # Планируем первое сохранение и обновление UI после задержки
        if delay_minutes > 0:
            timer = threading.Timer(delay_minutes * 60, force_update_and_save)
            timer.start()
            print(f"Запланировано первое обновление/сохранение через {delay_minutes} минут.")
        else:
            # Если задержка 0, сохраняем сразу
            save_state()

    except Exception as e:
        print(f"Ошибка отправки еженедельного сообщения: {e}")


def update_message_ui(force_save=False):
    """
    Редактирует сообщение в чате актуальными данными.
    Сохраняет состояние только если force_save=True (для первого обновления или ручной очистки).
    """
    if not current_session["active"] or not current_session["message_id"]:
        # Если сессия неактивна, не делаем ничего.
        return

    text = generate_message_text(current_session["queues"])
    keyboard = generate_keyboard(current_session["config"].get("teachers", []))

    try:
        bot.edit_message_text(
            chat_id=current_session["chat_id"],
            message_id=current_session["message_id"],
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        # Сохранение состояния только если запрошено (первое обновление, ручная очистка)
        if force_save:
            save_state()
    except Exception as e:
        # 'message is not modified' - обычная ошибка при отсутствии изменений
        if "message is not modified" not in str(e):
            print(f"Ошибка обновления интерфейса: {e}")
        # Сохраняем состояние, даже если UI не обновился (данные в памяти актуальны)
        if force_save:
            save_state()

        # --- ОБРАБОТЧИК КНОПОК ---


@bot.callback_query_handler(func=lambda call: call.data.startswith("join_"))
def handle_query(call):
    if not current_session["active"]:
        bot.answer_callback_query(call.id, "Эта очередь не активна.")
        return

    selected_teacher = call.data.replace("join_", "")
    user = call.from_user
    user_id = user.id
    display_name = get_user_display_name(user)

    # 1. Поиск: записан ли уже юзер куда-то?
    current_teacher_queue = None

    with queue_lock:
        for t_name, users_list in current_session["queues"].items():
            for u in users_list:
                if u['id'] == user_id:
                    current_teacher_queue = t_name
                    break
            if current_teacher_queue:
                break

        # 2. Логика кнопок

        # Сценарий А: Юзер нажал на ТОГО ЖЕ препода, к которому записан -> УДАЛЕНИЕ
        if current_teacher_queue == selected_teacher:
            # Удаляем пользователя
            current_session["queues"][selected_teacher] = [
                u for u in current_session["queues"][selected_teacher] if u['id'] != user_id
            ]
            bot.answer_callback_query(call.id, f"Вы больше не в очереди к: {selected_teacher}")

        # Сценарий Б: Юзер записан к ДРУГОМУ преподу -> ОШИБКА
        elif current_teacher_queue is not None:
            bot.answer_callback_query(call.id, f"Вы уже в очереди к: {current_teacher_queue}. Сначала выйдите оттуда.",
                                      show_alert=True)
            return

            # Сценарий В: Юзер никуда не записан -> ДОБАВЛЕНИЕ
        else:
            new_entry = {'id': user_id, 'display_name': display_name}
            current_session["queues"][selected_teacher].append(new_entry)

            # Вычисляем позицию
            position = len(current_session["queues"][selected_teacher])
            bot.answer_callback_query(call.id, f"Вы добавлены в очередь! Ваше текущее место: {position}",
                                      show_alert=True)

    # 3. Обновление сообщения (только если прошло n минут)

    # Получаем данные для проверки задержки из текущей активной сессии
    tz_str = current_session["config"].get("timezone", "UTC")
    start_time = current_session["start_time"]
    delay_minutes = current_session["config"].get("delay", 0)

    is_delayed_period = False
    if start_time and delay_minutes > 0:
        now = datetime.now(pytz_timezone(tz_str))
        is_delayed_period = (now - start_time).total_seconds() < (delay_minutes * 60)

    if not is_delayed_period:
        # Обновляем UI сразу после прохождения задержки (без сохранения, т.к. есть часовой джоб)
        update_message_ui()
        # Если идет период задержки, UI не обновляем.


# --- ФУНКЦИИ РУЧНОЙ ОЧИСТКИ ---

def clear_queues_and_update():
    """Стирает все очереди и обновляет сообщение в Telegram, принудительно сохраняя состояние."""
    global current_session

    if not current_session["active"]:
        print("Ошибка: Активная сессия очереди отсутствует. Очистка невозможна.")
        return

    with queue_lock:
        # Сброс очередей
        teachers = current_session["config"].get("teachers", [])
        current_session["queues"] = {t: [] for t in teachers}

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Очередь очищена вручную через консоль.")
    # Принудительное обновление UI и сохранение пустого состояния
    update_message_ui(force_save=True)


def console_listener_thread():
    """Отдельный поток для прослушивания консольного ввода (для команд 'clear' и 'save')."""
    print("\n--- СЕРВЕР ---")
    print("Введите 'clear' для ручной очистки очереди и 'save' для принудительного сохранения.")
    while True:
        try:
            command = input("> ").strip().lower()
            if command == "clear":
                clear_queues_and_update()
            elif command == "save":
                # Просто принудительно сохраняем текущее состояние
                save_state()
            elif command in ["exit", "quit"]:
                # Принудительное завершение
                os._exit(0)
            else:
                print("Неизвестная команда.")
        except EOFError:
            # Предотвращение краха, если консоль закрыта
            break
        except Exception as e:
            print(f"Ошибка консольного ввода: {e}")


if __name__ == "__main__":
    config = load_config()
    if not config: exit("Нет конфига")

    chat_id = config.get("chat_id")
    topic_id = config.get("topic_id")
    teachers = config.get("teachers", [])
    schedule_day = config.get("schedule_day")
    schedule_time_str = config.get("schedule_time")
    timezone_str = config.get("timezone")
    # Читаем оба параметра задержки
    update_delay = config.get("update_delay_minutes", 0)
    save_interval = config.get("save_delay_minutes", 60) # <--- Чтение интервала автосохранения

    # Проверка обязательных полей
    if not all([chat_id, schedule_day, schedule_time_str, timezone_str]):
        print("Ошибка: Проверьте config.json - отсутствуют обязательные поля.")
        exit(1)

    try:
        datetime.strptime(schedule_time_str, "%H:%M")
    except ValueError:
        print("Ошибка формата времени.")
        exit(1)

    # 1. Загрузка состояния при старте
    load_state()

    # 2. Настройка планировщика
    scheduler = BackgroundScheduler(timezone=timezone_str)


    # Обертка для запуска джобы
    def job_wrapper():
        # Перечитываем конфиг, чтобы использовать актуальный список преподов и ID
        curr_cfg = load_config()
        send_weekly_message(
            curr_cfg["chat_id"],
            curr_cfg["topic_id"],
            curr_cfg["teachers"],
            curr_cfg["timezone"],
            curr_cfg.get("update_delay_minutes", 0)  # Передаем задержку
        )


    # Еженедельная задача (создание нового сообщения с очередью)
    trigger_weekly = CronTrigger(
        day_of_week=schedule_day,
        hour=datetime.strptime(schedule_time_str, "%H:%M").hour,
        minute=datetime.strptime(schedule_time_str, "%H:%M").minute,
        timezone=timezone_str
    )
    scheduler.add_job(job_wrapper, trigger=trigger_weekly, id="weekly_queue")

    # Задача для периодического сохранения состояния (используем save_interval)
    scheduler.add_job(save_state, 'interval', minutes=save_interval, id="periodic_save") # <--- Использование save_interval

    print(f"Бот запущен. Расписание: {schedule_day} {schedule_time_str} ({timezone_str})")
    print(f"Задержка обновления UI/первого сохранения: {update_delay} мин.")
    print(f"Интервал автосохранения на диск: {save_interval} мин.") # <--- Добавила вывод для контроля

    scheduler.start()

    # 3. Запуск слушателя консоли в отдельном потоке
    console_thread = threading.Thread(target=console_listener_thread)
    console_thread.daemon = True
    console_thread.start()

    # 4. Запуск бота (блокирующий вызов)
    try:
        bot.infinity_polling()
    except (KeyboardInterrupt, SystemExit):
        print("Остановка бота...")
        scheduler.shutdown()