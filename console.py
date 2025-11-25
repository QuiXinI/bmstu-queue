import threading
import os
import queue_logic
import state_manager

def console_listener_thread():
    """Отдельный поток для прослушивания консольного ввода (для команд 'clear' и 'save')."""
    print("\n--- СЕРВЕР ---")
    print("Введите 'clear' для ручной очистки очереди и 'save' для принудительного сохранения.")
    while True:
        try:
            command = input("> ").strip().lower()
            if command == "clear":
                # Команда очистки очереди и обновления UI
                queue_logic.clear_queues_and_update()
            elif command == "save":
                # Команда принудительного сохранения состояния
                state_manager.save_state()
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
