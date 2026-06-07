import customtkinter as ctk
from tkinter import filedialog
import threading
from pathlib import Path

from subtitle_engine import SubtitleEngine

# Настройки внешнего вида CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("SubtitleMaker by AI")
        self.geometry("700x600")
        
        # Состояния приложения
        self.files_to_process = []
        self.engine = None
        self.is_running = False

        # --- UI ЭЛЕМЕНТЫ ---
        
        # Заголовок
        self.title_label = ctk.CTkLabel(self, text="Автоматическая генерация субтитров", font=ctk.CTkFont(size=20, weight="bold"))
        self.title_label.pack(pady=(15, 10))

        # Фрейм для выбора файлов и папок
        self.files_frame = ctk.CTkFrame(self)
        self.files_frame.pack(fill="x", padx=20, pady=5)
        
        self.btn_select_files = ctk.CTkButton(self.files_frame, text="Выбрать видео...", command=self.select_files)
        self.btn_select_files.grid(row=0, column=0, padx=10, pady=10)
        
        self.btn_select_folder = ctk.CTkButton(self.files_frame, text="Выбрать папку...", command=self.select_folder)
        self.btn_select_folder.grid(row=0, column=1, padx=10, pady=10)
        
        self.files_info_label = ctk.CTkLabel(self.files_frame, text="Файлы не выбраны")
        self.files_info_label.grid(row=0, column=2, padx=10, pady=10, sticky="w")

        # Фрейм настроек (Модель и Перезапись)
        self.settings_frame = ctk.CTkFrame(self)
        self.settings_frame.pack(fill="x", padx=20, pady=10)

        self.model_label = ctk.CTkLabel(self.settings_frame, text="Размер модели:")
        self.model_label.grid(row=0, column=0, padx=(10, 5), pady=10)

        self.model_var = ctk.StringVar(value="small")
        self.model_dropdown = ctk.CTkOptionMenu(
            self.settings_frame, 
            variable=self.model_var,
            values=["tiny", "base", "small", "medium", "large-v3"]
        )
        self.model_dropdown.grid(row=0, column=1, padx=5, pady=10)

        self.overwrite_var = ctk.BooleanVar(value=False)
        self.checkbox_overwrite = ctk.CTkCheckBox(self.settings_frame, text="Перезаписывать существующие", variable=self.overwrite_var)
        self.checkbox_overwrite.grid(row=0, column=2, padx=20, pady=10)

        self.gpu_var = ctk.BooleanVar(value=True)
        self.checkbox_gpu = ctk.CTkCheckBox(self.settings_frame, text="Использовать видеокарту (GPU)", variable=self.gpu_var)
        self.checkbox_gpu.grid(row=0, column=3, padx=20, pady=10)

        # Лог (Текстовое поле)
        self.log_textbox = ctk.CTkTextbox(self, width=660, height=250, state="disabled")
        self.log_textbox.pack(padx=20, pady=5, fill="both", expand=True)

        # Фрейм для кнопок управления (Старт / Стоп)
        self.controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.controls_frame.pack(fill="x", padx=20, pady=(10, 20))
        
        self.btn_start = ctk.CTkButton(self.controls_frame, text="Старт", command=self.start_processing, fg_color="green", hover_color="darkgreen")
        self.btn_start.pack(side="left", expand=True, fill="x", padx=(0, 10))
        
        self.btn_stop = ctk.CTkButton(self.controls_frame, text="Стоп / Отмена", command=self.stop_processing, fg_color="red", hover_color="darkred", state="disabled")
        self.btn_stop.pack(side="right", expand=True, fill="x", padx=(10, 0))

        self.log_message("Добро пожаловать. Приложение готово к работе! FFmpeg встроен.")

    # --- ЛОГИКА ИНТЕРФЕЙСА ---

    def log_message(self, message):
        """Потокобезопасное добавление логов в текстовое поле."""
        self.after(0, self._append_to_textbox, message)

    def _append_to_textbox(self, message):
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", f"{message}\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def select_files(self):
        filetypes = (("Video files", "*.mp4 *.avi *.mkv *.mov"), ("All files", "*.*"))
        filenames = filedialog.askopenfilenames(title="Выберите видео", filetypes=filetypes)
        if filenames:
            self.files_to_process = list(filenames)
            self.files_info_label.configure(text=f"Выбрано файлов: {len(self.files_to_process)}")
            self.log_message(f"Выбрано файлов: {len(self.files_to_process)}")

    def select_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку с видео")
        if folder:
            path = Path(folder)
            # Ищем популярные расширения
            exts = ('.mp4', '.avi', '.mkv', '.mov')
            self.files_to_process = [str(p) for p in path.iterdir() if p.suffix.lower() in exts]
            self.files_info_label.configure(text=f"Найдено видео: {len(self.files_to_process)}")
            self.log_message(f"В папке найдено видео: {len(self.files_to_process)}")

    def start_processing(self):
        if not self.files_to_process:
            self.log_message("Ошибка: Сначала выберите видео файлы или папку.")
            return

        if self.is_running:
            return

        # Обновляем UI
        self.is_running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_select_files.configure(state="disabled")
        self.btn_select_folder.configure(state="disabled")
        
        # Создаем и запускаем движок в отдельном потоке
        self.engine = SubtitleEngine(
            model_size=self.model_var.get(),
            use_gpu=self.gpu_var.get(),
            log_callback=self.log_message
        )
        t = threading.Thread(target=self._process_thread, daemon=True)
        t.start()

    def _process_thread(self):
        """Метод, выполняющийся в фоновом потоке."""
        try:
            self.engine.process_videos(self.files_to_process, self.overwrite_var.get())
        except Exception as e:
            self.log_message(f"Критическая ошибка: {e}")
        finally:
            self.after(0, self._on_process_finished)

    def _on_process_finished(self):
        """Возвращает UI в исходное состояние после завершения."""
        self.is_running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_select_files.configure(state="normal")
        self.btn_select_folder.configure(state="normal")

    def stop_processing(self):
        if self.engine and self.is_running:
            self.log_message("Остановка... Дождитесь завершения текущего цикла.")
            self.engine.stop()

if __name__ == "__main__":
    app = App()
    app.mainloop()