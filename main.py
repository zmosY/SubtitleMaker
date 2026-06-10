import customtkinter as ctk
from tkinter import filedialog
import threading
from pathlib import Path
import pyperclip
import hashlib
import subprocess
import os
from PIL import Image
from subtitle_engine import SubtitleEngine

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SubtitleMaker by AI")
        self.geometry("900x650")
        self.minsize(800, 600)
        self.files_to_process = []
        self.engine = None
        self.is_running = False
        self.selected_video_path = None
        self.video_widgets = {}
        self.thumbnails_dir = Path(__file__).parent / "thumbnails"
        self.thumbnails_dir.mkdir(exist_ok=True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.left_panel = ctk.CTkFrame(self)
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(10,5), pady=10)
        self.left_panel.grid_rowconfigure(2, weight=1)
        self.left_panel.grid_columnconfigure(0, weight=1)

        # Заголовок с переключателем режима просмотра
        header_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=10, pady=(10,5), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(header_frame, text="📁 Видеофайлы", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        
        self.view_mode_var = ctk.StringVar(value="👁️ Превью")
        self.view_toggle = ctk.CTkSegmentedButton(
            header_frame,
            values=["👁️ Превью", "📋 Список"],
            variable=self.view_mode_var,
            command=self._on_view_mode_changed,
            height=26,
            width=180
        )
        self.view_toggle.grid(row=0, column=1, padx=(10,0))
        
        btn_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=10, pady=(0,5), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        self.btn_select_files = ctk.CTkButton(btn_frame, text="Выбрать видео...", command=self.select_files, height=30)
        self.btn_select_files.grid(row=0, column=0, padx=(0,5), pady=5, sticky="ew")
        self.btn_select_folder = ctk.CTkButton(btn_frame, text="Выбрать папку...", command=self.select_folder, height=30)
        self.btn_select_folder.grid(row=0, column=1, padx=(5,0), pady=5, sticky="ew")

        self.videos_scroll = ctk.CTkScrollableFrame(self.left_panel)
        self.videos_scroll.grid(row=2, column=0, padx=10, pady=(5,10), sticky="nsew")
        self.videos_scroll.grid_columnconfigure(0, weight=1)

        self.right_panel = ctk.CTkFrame(self)
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(5,10), pady=10)
        self.right_panel.grid_rowconfigure(2, weight=1)
        self.right_panel.grid_columnconfigure(0, weight=1)

        self.preview_title = ctk.CTkLabel(self.right_panel, text="📝 Предпросмотр", font=ctk.CTkFont(size=14, weight="bold"))
        self.preview_title.grid(row=0, column=0, padx=10, pady=(10,5), sticky="w")

        lang_frame = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        lang_frame.grid(row=1, column=0, padx=10, pady=(0,5), sticky="ew")
        self.lang_var = ctk.StringVar(value="ru")
        self.lang_btn = ctk.CTkSegmentedButton(lang_frame, values=["RU","EN"], variable=self.lang_var, command=self._on_lang_changed, height=28)
        self.lang_btn.pack(side="left", padx=(0,10))
        self.copy_btn = ctk.CTkButton(lang_frame, text="📋 Копировать", command=self._copy_subtitle, width=100, height=28, fg_color="#28a745")
        self.copy_btn.pack(side="right")

        self.preview_box = ctk.CTkTextbox(self.right_panel, state="disabled", font=ctk.CTkFont(family="Consolas", size=10))
        self.preview_box.grid(row=2, column=0, padx=10, pady=(5,10), sticky="nsew")

        self.bottom = ctk.CTkFrame(self)
        self.bottom.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0,10))
        
        ctk.CTkLabel(self.bottom, text="Модель:").grid(row=0, column=0, padx=(10,5), pady=10)
        self.model_var = ctk.StringVar(value="small")
        ctk.CTkOptionMenu(self.bottom, variable=self.model_var, values=["tiny","base","small","medium","large-v3"], width=90).grid(row=0, column=1, padx=5, pady=10)
        self.overwrite_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.bottom, text="Перезапись", variable=self.overwrite_var, width=120).grid(row=0, column=2, padx=10, pady=10)
        self.gpu_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.bottom, text="GPU", variable=self.gpu_var, width=70).grid(row=0, column=3, padx=10, pady=10)
        
        ctrl_frame = ctk.CTkFrame(self.bottom, fg_color="transparent")
        ctrl_frame.grid(row=0, column=4, padx=(20,10), pady=10)
        self.btn_start = ctk.CTkButton(ctrl_frame, text="▶ Старт", command=self.start_processing, fg_color="green", width=90)
        self.btn_start.pack(side="left", padx=(0,5))
        self.btn_stop = ctk.CTkButton(ctrl_frame, text="⏹ Стоп", command=self.stop_processing, fg_color="red", state="disabled", width=90)
        self.btn_stop.pack(side="left", padx=(5,0))

        self.log_box = ctk.CTkTextbox(self, height=100, state="disabled", font=ctk.CTkFont(size=9))
        self.log_box.grid(row=2, column=0, columnspan=2, padx=10, pady=(0,10), sticky="ew")
        self.log_message("Готово! Выберите видео для работы.")

    def log_message(self, msg):
        self.after(0, lambda: self._append_log(msg))

    def _append_log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _add_video_item(self, video_path: str):
        path = Path(video_path)
        has_srt = path.with_name(f"{path.stem}_ru.srt").exists() or path.with_name(f"{path.stem}_en.srt").exists()
        if self.view_mode_var.get() == "👁️ Превью":
            self._add_video_card(video_path, path, has_srt)
        else:
            self._add_video_row(video_path, path, has_srt)

    def _add_video_row(self, video_path: str, path: Path, has_srt: bool):
        """Компактный элемент для режима списка."""
        item = ctk.CTkFrame(self.videos_scroll, fg_color="transparent")
        item.grid_columnconfigure(0, weight=1)
        item.pack(fill="x", pady=2, padx=5)
        def on_click():
            self.selected_video_path = video_path
            self.preview_title.configure(text=f"📝 {path.name}")
            self._load_subtitle(video_path)
        btn = ctk.CTkButton(item, text=f"🎬 {path.name}", command=on_click, anchor="w", fg_color="transparent", border_width=1, border_color="#444", hover_color="#3a3a3a", height=30)
        btn.grid(row=0, column=0, sticky="ew", padx=(0,5))
        status = ctk.CTkLabel(item, text="✅" if has_srt else "⏳", width=25, text_color="#4ade80" if has_srt else "#94a3b8")
        status.grid(row=0, column=1)
        def remove():
            if video_path in self.files_to_process:
                self.files_to_process.remove(video_path)
            item.destroy()
            if self.selected_video_path == video_path:
                self._clear_preview()
        ctk.CTkButton(item, text="✕", command=remove, width=25, height=25, fg_color="#ef4444", hover_color="#dc2626").grid(row=0, column=2, padx=(5,0))
        self.video_widgets[video_path] = item

    def _add_video_card(self, video_path: str, path: Path, has_srt: bool):
        """Карточка с превью-изображением для режима предпросмотра."""
        card = ctk.CTkFrame(self.videos_scroll, fg_color="#1e1e1e", border_width=1, border_color="#3a3a3a", corner_radius=8)
        card.pack(fill="x", pady=4, padx=5)
        card.grid_columnconfigure(1, weight=1)

        def on_click():
            self.selected_video_path = video_path
            self.preview_title.configure(text=f"📝 {path.name}")
            self._load_subtitle(video_path)

        # Превью-изображение (CTkButton — чтобы клик работал в CustomTkinter)
        thumb_img = self._load_thumbnail(video_path)
        thumb_btn = ctk.CTkButton(
            card, image=thumb_img, text="", command=on_click,
            width=142, height=80, fg_color="#2a2a2a", hover_color="#3a3a3a",
            corner_radius=6, border_width=0, cursor="hand2"
        )
        thumb_btn.grid(row=0, column=0, rowspan=2, padx=(8,10), pady=8)

        # Название файла (кликабельная кнопка)
        name_btn = ctk.CTkButton(
            card, text=path.name, command=on_click,
            anchor="w", fg_color="transparent", hover_color="#2a2a2a",
            font=ctk.CTkFont(size=13, weight="bold"), height=20,
            border_width=0, cursor="hand2"
        )
        name_btn.grid(row=0, column=1, sticky="ew", pady=(12,0))

        # Статус
        status_text = "✅ Субтитры готовы" if has_srt else "⏳ Ожидает обработки"
        ctk.CTkLabel(card, text=status_text, text_color="#4ade80" if has_srt else "#94a3b8",
                     font=ctk.CTkFont(size=11), anchor="w").grid(row=1, column=1, sticky="w", pady=(2,10))

        # Кнопка удаления
        def remove():
            if video_path in self.files_to_process:
                self.files_to_process.remove(video_path)
            card.destroy()
            if self.selected_video_path == video_path:
                self._clear_preview()
        ctk.CTkButton(card, text="✕", command=remove, width=26, height=26,
                      fg_color="transparent", hover_color="#ef4444", text_color="#888",
                      corner_radius=6, cursor="hand2").grid(row=0, column=2, rowspan=2, padx=(5,8))

        self.video_widgets[video_path] = card

    def _load_thumbnail(self, video_path: str):
        """Загружает или создаёт превью для видео с кешированием.
        Извлекает кадр на ~20% длительности видео (но не раньше 3 сек).
        """
        import imageio_ffmpeg

        video_hash = hashlib.md5(video_path.encode()).hexdigest()
        thumb_path = self.thumbnails_dir / f"{video_hash}.jpg"

        if not thumb_path.exists():
            try:
                ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
                # Надёжно строим путь к ffprobe
                ffprobe_bin = os.path.join(
                    os.path.dirname(ffmpeg_bin),
                    'ffprobe' + os.path.splitext(ffmpeg_bin)[1]
                )
                seek_time = 10.0  # fallback
                try:
                    result = subprocess.run(
                        [ffprobe_bin, '-v', 'error', '-show_entries', 'format=duration',
                         '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                        capture_output=True, text=True, timeout=15
                    )
                    if result.stdout.strip():
                        video_duration = float(result.stdout.strip())
                        seek_time = max(3.0, video_duration * 0.2)
                except Exception:
                    pass

                seek_time = min(seek_time, 60.0)  # не дальше 60 сек
                subprocess.run(
                    [ffmpeg_bin, '-ss', str(seek_time), '-i', video_path,
                     '-vframes', '1', '-q:v', '3', '-vf', 'scale=284:160',
                     '-y', str(thumb_path)],
                    capture_output=True, timeout=30
                )
                # Если не получилось — пробуем первый кадр (для очень коротких видео)
                if not thumb_path.exists():
                    subprocess.run(
                        [ffmpeg_bin, '-ss', '0', '-i', video_path,
                         '-vframes', '1', '-q:v', '3', '-vf', 'scale=284:160',
                         '-y', str(thumb_path)],
                        capture_output=True, timeout=30
                    )
            except Exception as e:
                self.log_message(f"Не удалось создать превью для {Path(video_path).name}: {e}")

        if thumb_path.exists():
            pil_img = Image.open(thumb_path)
            return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(142, 80))
        else:
            placeholder = Image.new('RGB', (142, 80), color='#3a3a3a')
            return ctk.CTkImage(light_image=placeholder, dark_image=placeholder, size=(142, 80))

    def _load_subtitle(self, video_path: str):
        path = Path(video_path)
        lang = self.lang_var.get()
        srt_file = path.with_name(f"{path.stem}_{lang}.srt")
        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        if srt_file.exists():
            try:
                with open(srt_file, "r", encoding="utf-8") as f:
                    self.preview_box.insert("1.0", f.read())
            except Exception as e:
                self.preview_box.insert("1.0", f"Ошибка: {e}")
        else:
            self.preview_box.insert("1.0", f"Файл не найден:\n{path.stem}_{lang}.srt")
        self.preview_box.configure(state="disabled")

    def _clear_preview(self):
        self.preview_title.configure(text="📝 Предпросмотр")
        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", "Выберите видео для просмотра субтитров")
        self.preview_box.configure(state="disabled")

    def _on_view_mode_changed(self, value):
        """Переключает режим отображения списка видео."""
        # Очищаем и пересоздаём элементы списка
        for w in self.videos_scroll.winfo_children():
            w.destroy()
        self.video_widgets.clear()
        for v in self.files_to_process:
            self._add_video_item(v)
        
        # Применяем режим - value содержит выбранное значение из segmented button
        self._apply_view_mode_layout(value)
    
    def _apply_view_mode_layout(self, mode_value=None):
        """Применяет текущий режим отображения к интерфейсу."""
        if mode_value is None:
            mode_value = self.view_mode_var.get()
        
        if mode_value == "📋 Список":
            # Режим списка: скрываем правую панель
            self.right_panel.grid_remove()
        else:
            # Режим превью: показываем правую панель
            self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(5,10), pady=10)
            if self.selected_video_path:
                self._load_subtitle(self.selected_video_path)
        self.update_idletasks()

    def _on_lang_changed(self, _):
        if self.selected_video_path:
            self._load_subtitle(self.selected_video_path)

    def _copy_subtitle(self):
        content = self.preview_box.get("1.0", "end").strip()
        if content and "не найден" not in content and "Ошибка" not in content:
            pyperclip.copy(content)
            self.log_message("✅ Скопировано!")
        else:
            self.log_message("Нет данных для копирования")

    def select_files(self):
        files = filedialog.askopenfilenames(title="Выберите видео", filetypes=(("Video","*.mp4 *.avi *.mkv *.mov"),("All","*.*")))
        for f in files:
            if f not in self.files_to_process:
                self.files_to_process.append(f)
                self._add_video_item(f)
        if files:
            self.log_message(f"Добавлено: {len(files)}")

    def select_folder(self):
        folder = filedialog.askdirectory(title="Папка с видео")
        if folder:
            exts = ('.mp4','.avi','.mkv','.mov')
            videos = [str(p) for p in Path(folder).iterdir() if p.suffix.lower() in exts]
            count = 0
            for v in videos:
                if v not in self.files_to_process:
                    self.files_to_process.append(v)
                    self._add_video_item(v)
                    count += 1
            self.log_message(f"Найдено: {count}")

    def start_processing(self):
        if not self.files_to_process or self.is_running:
            return
        self.is_running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_select_files.configure(state="disabled")
        self.btn_select_folder.configure(state="disabled")
        self.engine = SubtitleEngine(model_size=self.model_var.get(), use_gpu=self.gpu_var.get(), log_callback=self.log_message)
        threading.Thread(target=self._process_thread, daemon=True).start()

    def _process_thread(self):
        try:
            self.engine.process_videos(self.files_to_process, self.overwrite_var.get())
        except Exception as e:
            self.log_message(f"Ошибка: {e}")
        finally:
            self.after(0, self._on_finished)

    def _on_finished(self):
        self.is_running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_select_files.configure(state="normal")
        self.btn_select_folder.configure(state="normal")
        for w in self.videos_scroll.winfo_children():
            w.destroy()
        self.video_widgets.clear()
        for v in self.files_to_process:
            self._add_video_item(v)
        if self.selected_video_path:
            self._load_subtitle(self.selected_video_path)

    def stop_processing(self):
        if self.engine and self.is_running:
            self.log_message("Остановка...")
            self.engine.stop()

if __name__ == "__main__":
    App().mainloop()