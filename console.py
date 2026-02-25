import os
import queue_logic
import state_manager

def console_listener_thread():
    """Отдельный поток для прослушивания консольного ввода (для команд 'clear' и 'save')."""
    print("\n--- КОНСОСЬ ---")
    print("Введите 'clear' для ручной очистки очереди и 'save' для принудительного сохранения.")
    while True:
        try:
            command = input("> ").strip().lower()
            if command in ["clear", "clean"]:
                queue_logic.clear_queues_and_update()
            elif command in ["save", "backup", "store", "safe", "ave", "sav", "save/"]:
                state_manager.save_state()
            elif command in ["exit", "quit"]:
                state_manager.save_state()
                os._exit(f"Вы выключили сервер командой {command}")
            else:
                print("Неизвестная команда.")
        except EOFError:
            # Предотвращение краша, если консоль закрыта
            break
        except Exception as e:
            print(f"Ошибка консольного ввода: {e}")
