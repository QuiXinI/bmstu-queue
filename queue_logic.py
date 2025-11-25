import threading
from datetime import datetime
from telebot import types
from pytz import timezone as pytz_timezone

import state_manager

bot_instance = None

def init_queue_logic(bot):
    """Инициализация экземпляра бота. Вызывается из main.py."""
    global bot_instance
    bot_instance = bot


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


# --- ОСНОВНЫЕ ФУНКЦИИ УПРАВЛЕНИЯ ОЧЕРЕДЬЮ ---
def force_update_and_save():
    """Принудительное обновление сообщения и сохранение состояния (срабатывает таймером)."""
    update_message_ui(force_save=True)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Период задержки прошел. Сообщение обновлено и состояние сохранено.")


def restore_delay_timer(current_session, force_update_and_save_cb):
    """Восстанавливает таймер задержки при запуске, если сессия активна."""
    if not current_session["active"] or not current_session["message_id"]:
        return

    # Данные для восстановления берутся из текущей сессии
    tz = current_session["config"].get("timezone", "UTC")
    start_time = current_session["start_time"]
    delay_minutes = current_session["config"].get("delay", 0)

    if start_time and delay_minutes > 0:
        now = datetime.now(pytz_timezone(tz))
        time_passed_seconds = (now - start_time).total_seconds()
        remaining_time_seconds = (delay_minutes * 60) - time_passed_seconds

        if remaining_time_seconds > 5:
            timer = threading.Timer(remaining_time_seconds, force_update_and_save_cb)
            timer.start()
            print(
                f"Возобновлен таймер. Запланировано первое обновление/сохранение через {int(remaining_time_seconds)} секунд.")
        else:
            # Если время вышло, запускаем немедленно
            force_update_and_save_cb()


def send_weekly_message(chat_id, topic_id, teachers, tz, delay_minutes, keep_old_queue_setting):
    """
    Отправляет новое сообщение с очередью по расписанию.
    Если keep_old_queue_setting == 1, подтягивает записи из предыдущей очереди.
    """
    current_session = state_manager.get_current_session()

    keep_old_queue = keep_old_queue_setting == 1

    previous_queues = current_session["queues"]
    new_queues = {t: [] for t in teachers}

    if keep_old_queue:
        for teacher in teachers:
            users_to_keep = previous_queues.get(teacher, [])
            new_queues[teacher] = users_to_keep

    with state_manager.queue_lock:
        current_session["queues"] = new_queues
        current_session["active"] = True
        current_session["start_time"] = datetime.now(pytz_timezone(tz))
        current_session["chat_id"] = chat_id
        current_session["config"] = {
            "teachers": teachers,
            "timezone": tz,
            "delay": delay_minutes
        }

    text = generate_message_text(current_session["queues"])
    keyboard = generate_keyboard(teachers)

    try:
        msg = bot_instance.send_message(
            chat_id,
            text,
            message_thread_id=topic_id,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        current_session["message_id"] = msg.message_id
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Новая очередь открыта. ID сообщения: {msg.message_id}")
        if keep_old_queue:
            print("Предыдущий порядок очереди был сохранен.")
        else:
            print("Очередь сброшена.")

        if delay_minutes > 0:
            timer = threading.Timer(delay_minutes * 60, force_update_and_save)
            timer.start()
            print(f"Запланировано первое обновление/сохранение через {delay_minutes} минут.")
        else:
            state_manager.save_state()

    except Exception as e:
        print(f"Ошибка отправки еженедельного сообщения: {e}")


def update_message_ui(force_save=False):
    """Редактирует сообщение в чате актуальными данными."""
    current_session = state_manager.get_current_session()

    if not current_session["active"] or not current_session["message_id"]:
        return

    text = generate_message_text(current_session["queues"])
    keyboard = generate_keyboard(current_session["config"].get("teachers", []))

    try:
        bot_instance.edit_message_text(
            chat_id=current_session["chat_id"],
            message_id=current_session["message_id"],
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        if force_save:
            state_manager.save_state()
    except Exception as e:
        if "message is not modified" not in str(e):
            print(f"Ошибка обновления интерфейса: {e}")
        if force_save:
            state_manager.save_state()


def handle_query(call):
    """Обработчик нажатий на кнопки очереди."""
    current_session = state_manager.get_current_session()
    if not current_session["active"]:
        bot_instance.answer_callback_query(call.id, "Эта очередь не активна.")
        return

    selected_teacher = call.data.replace("join_", "")
    user = call.from_user
    user_id = user.id
    display_name = get_user_display_name(user)

    current_teacher_queue = None

    with state_manager.queue_lock:
        # 1. Поиск: записан ли уже юзер куда-то?
        for t_name, users_list in current_session["queues"].items():
            for u in users_list:
                if u['id'] == user_id:
                    current_teacher_queue = t_name
                    break
            if current_teacher_queue:
                break

        # 2. Логика кнопок
        if current_teacher_queue == selected_teacher:
            # Сценарий А: Удаление
            current_session["queues"][selected_teacher] = [
                u for u in current_session["queues"][selected_teacher] if u['id'] != user_id
            ]
            bot_instance.answer_callback_query(call.id, f"Вы больше не в очереди к: {selected_teacher}")

        elif current_teacher_queue is not None:
            # Сценарий Б: Ошибка (уже в другой очереди)
            bot_instance.answer_callback_query(call.id,
                                               f"Вы уже в очереди к: {current_teacher_queue}. Сначала выйдите оттуда.",
                                               show_alert=True)
            return

        else:
            # Сценарий В: Добавление
            new_entry = {'id': user_id, 'display_name': display_name}
            current_session["queues"][selected_teacher].append(new_entry)
            position = len(current_session["queues"][selected_teacher])
            bot_instance.answer_callback_query(call.id, f"Вы добавлены в очередь! Ваше текущее место: {position}",
                                               show_alert=True)

    # 3. Обновление сообщения (только если прошло update_delay_minutes минут)
    tz_str = current_session["config"].get("timezone", "UTC")
    start_time = current_session["start_time"]
    delay_minutes = current_session["config"].get("delay", 0)

    is_delayed_period = False
    if start_time and delay_minutes > 0:
        now = datetime.now(pytz_timezone(tz_str))
        is_delayed_period = (now - start_time).total_seconds() < (delay_minutes * 60)

    if not is_delayed_period:
        update_message_ui()


def clear_queues_and_update():
    """Стирает все очереди и обновляет сообщение в Telegram, принудительно сохраняя состояние."""
    current_session = state_manager.get_current_session()

    if not current_session["active"]:
        print("Ошибка: Активная сессия очереди отсутствует. Очистка невозможна.")
        return

    with state_manager.queue_lock:
        teachers = current_session["config"].get("teachers", [])
        current_session["queues"] = {t: [] for t in teachers}

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Очередь очищена вручную через консоль.")
    update_message_ui(force_save=True)
