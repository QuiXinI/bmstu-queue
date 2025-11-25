import os
import threading
from datetime import datetime

import telebot
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config_loader
import state_manager
import queue_logic
import console

# --- КОНСТАНТЫ И ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("Ошибка: BOT_TOKEN не найден в файле .env")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

queue_logic.init_queue_logic(bot)


# --- ГЛАВНАЯ ЛОГИКА ---
if __name__ == "__main__":
    config = config_loader.load_config()
    if not config: exit("Нет конфига")

    chat_id = config.get("chat_id")
    topic_id = config.get("topic_id", 0)
    teachers = config.get("teachers")
    schedule_day = config.get("schedule_day", "mon")
    schedule_time_str = config.get("schedule_time", "18:00")
    timezone_str = config.get("timezone", "Europe/Moscow")
    update_delay = config.get("update_delay_minutes", 0)
    save_interval = config.get("save_delay_minutes", 60)
    keep_queue = config.get("keep_previous_queue", 0)

    # Проверка обязательных полей
    if not all([chat_id, teachers]):
        print("Ошибка: Проверьте config.json - отсутствуют обязательные поля.")
        exit(1)

    try:
        datetime.strptime(schedule_time_str, "%H:%M")
    except ValueError:
        print("Ошибка формата времени.")
        exit(1)

    state_manager.load_state()
    session = state_manager.get_current_session()
    queue_logic.restore_delay_timer(session, queue_logic.force_update_and_save)

    scheduler = BackgroundScheduler(timezone=timezone_str)

    def job_wrapper():
        curr_cfg = config_loader.load_config()
        keep_queue_setting = curr_cfg.get("keep_previous_queue", 0)

        queue_logic.send_weekly_message(
            curr_cfg["chat_id"],
            curr_cfg["topic_id"],
            curr_cfg["teachers"],
            curr_cfg["timezone"],
            curr_cfg.get("update_delay_minutes", 0),
            keep_queue_setting
        )

    trigger_weekly = CronTrigger(
        day_of_week=schedule_day,
        hour=datetime.strptime(schedule_time_str, "%H:%M").hour,
        minute=datetime.strptime(schedule_time_str, "%H:%M").minute,
        timezone=timezone_str
    )
    scheduler.add_job(job_wrapper, trigger=trigger_weekly, id="weekly_queue")

    scheduler.add_job(state_manager.save_state, 'interval', minutes=save_interval, id="periodic_save")

    print(f"Бот запущен. Расписание: {schedule_day} {schedule_time_str} ({timezone_str})")
    print(f"Задержка обновления UI/первого сохранения: {update_delay} мин.")
    print(f"Интервал автосохранения на диск: {save_interval} мин.")
    print(f"Перенос старой очереди: {'Включен' if keep_queue == 1 else 'Отключен'}")

    scheduler.start()

    bot.callback_query_handler(func=lambda call: call.data.startswith("join_"))(queue_logic.handle_query)

    console_thread = threading.Thread(target=console.console_listener_thread)
    console_thread.daemon = True
    console_thread.start()

    try:
        bot.infinity_polling()
    except (KeyboardInterrupt, SystemExit):
        print("Остановка бота...")
        scheduler.shutdown()
