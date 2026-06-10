import customtkinter as ctk
from tkinter import filedialog
import threading
from pathlib import Path
import pyperclip
import hashlib
import subprocess
import os
import time
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
        self.videos_scroll.bind("<Configure>", self._on_scroll_configure)

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

        # --- Нижняя панель: прогресс + кнопки + доп.настройки ---
        self.bottom_frame = ctk.CTkFrame(self)
        self.bottom_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0,5))

        # === Верхняя строка: всегда видна ===
        self.bar_row = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        self.bar_row.pack(fill="x", padx=6, pady=(6,2))

        # Прогресс-бар
        self.progress_bar = ctk.CTkProgressBar(self.bar_row, height=14, corner_radius=7,
                                                progress_color="#4a90d9",
                                                fg_color="#2a2a2a")
        self.progress_bar.set(0)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0,10))

        # Проценты + время
        self.progress_label = ctk.CTkLabel(self.bar_row, text="0%  --:--",
                                           width=80, font=ctk.CTkFont(size=12),
                                           anchor="center")
        self.progress_label.pack(side="left", padx=(0,12))

        # Кнопки Старт / Стоп
        ctrl_frame = ctk.CTkFrame(self.bar_row, fg_color="transparent")
        ctrl_frame.pack(side="left")
        self.btn_start = ctk.CTkButton(ctrl_frame, text="▶ Старт", command=self.start_processing,
                                       fg_color="green", width=78, height=28)
        self.btn_start.pack(side="left", padx=(0,4))
        self.btn_stop = ctk.CTkButton(ctrl_frame, text="⏹ Стоп", command=self.stop_processing,
                                      fg_color="red", state="disabled", width=78, height=28)
        self.btn_stop.pack(side="left")

        # Кнопка «Дополнительно»
        self.advanced_visible = False
        self.btn_advanced = ctk.CTkButton(
            self.bar_row, text="⚙ Дополнительно ▼", command=self._toggle_advanced,
            width=148, height=28, fg_color="transparent", border_width=1,
            border_color="#555", text_color="#aaa", hover_color="#2a2a2a",
            font=ctk.CTkFont(size=11), cursor="hand2"
        )
        self.btn_advanced.pack(side="right", padx=(8,0))

        # === Скрываемая панель доп.настроек ===
        self.advanced_frame = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")

        # Строка настроек: модель, перезапись, GPU
        settings_row = ctk.CTkFrame(self.advanced_frame, fg_color="transparent")
        settings_row.pack(fill="x", padx=8, pady=(6,2))

        ctk.CTkLabel(settings_row, text="Модель:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0,4))
        self.model_var = ctk.StringVar(value="small")
        self.model_menu = ctk.CTkOptionMenu(settings_row, variable=self.model_var,
                          values=["tiny", "base", "small", "medium", "large-v3", "azure"],
                          width=100, height=26, command=self._on_model_changed)
        self.model_menu.pack(side="left", padx=(0,15))
        self.overwrite_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(settings_row, text="Перезапись", variable=self.overwrite_var,
                        height=26, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0,10))
        self.gpu_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(settings_row, text="GPU", variable=self.gpu_var,
                        height=26, font=ctk.CTkFont(size=12)).pack(side="left")

        # Azure-настройки (показываются только при выборе "azure")
        self.azure_frame = ctk.CTkFrame(self.advanced_frame, fg_color="transparent")
        ctk.CTkLabel(self.azure_frame, text="Azure Key:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(0,4))
        self.azure_key_var = ctk.StringVar()
        ctk.CTkEntry(self.azure_frame, textvariable=self.azure_key_var, width=180,
                     height=26, font=ctk.CTkFont(size=11), show="*").pack(side="left", padx=(0,10))
        ctk.CTkLabel(self.azure_frame, text="Region:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(0,4))
        self.azure_region_var = ctk.StringVar(value="eastus")
        ctk.CTkEntry(self.azure_frame, textvariable=self.azure_region_var, width=100,
                     height=26, font=ctk.CTkFont(size=11)).pack(side="left")
        # Лог (консоль)
        self.log_box = ctk.CTkTextbox(self.advanced_frame, height=90,
                                      state="disabled", font=ctk.CTkFont(family="Consolas", size=9))
        self.log_box.pack(fill="x", padx=8, pady=(4,8))

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

    _TILE_W = 160   # ширина превью в плитке
    _TILE_H = 100   # высота превью в плитке
    _TILE_GAP = 8   # зазор между плитками

    def _add_video_card(self, video_path: str, path: Path, has_srt: bool):
        """Плитка с превью для режима предпросмотра (grid-сетка)."""
        tile = ctk.CTkFrame(self.videos_scroll, fg_color="#1e1e1e",
                            border_width=1, border_color="#3a3a3a", corner_radius=8)

        def on_click():
            self.selected_video_path = video_path
            self.preview_title.configure(text=f"📝 {path.name}")
            self._load_subtitle(video_path)

        # --- Контейнер для превью (чтобы можно было наложить бейдж) ---
        thumb_holder = ctk.CTkFrame(tile, fg_color="transparent",
                                    width=self._TILE_W, height=self._TILE_H)
        thumb_holder.pack(padx=8, pady=(8,2))
        thumb_holder.pack_propagate(False)

        # Превью-изображение
        thumb_img = self._load_thumbnail(video_path)
        thumb_btn = ctk.CTkButton(
            thumb_holder, image=thumb_img, text="", command=on_click,
            fg_color="#2a2a2a", hover_color="#3a3a3a",
            corner_radius=6, border_width=0, cursor="hand2"
        )
        thumb_btn.place(x=0, y=0, relwidth=1, relheight=1)

        # Бейдж статуса — правый нижний угол превью
        badge_text = "✅" if has_srt else "✕"
        badge_color = "#4ade80" if has_srt else "#ef4444"
        badge = ctk.CTkLabel(
            thumb_holder, text=badge_text,
            fg_color="#222222", text_color=badge_color,
            corner_radius=5, width=26, height=26,
            font=ctk.CTkFont(size=14)
        )
        badge.place(relx=1.0, rely=1.0, anchor="se", x=-4, y=-4)

        # Кнопка удаления — правый верхний угол превью
        def remove():
            if video_path in self.files_to_process:
                self.files_to_process.remove(video_path)
            tile.destroy()
            if self.selected_video_path == video_path:
                self._clear_preview()
            self._reflow_tiles()
        rm_btn = ctk.CTkButton(
            thumb_holder, text="✕", command=remove,
            width=22, height=22, fg_color="#cc3333", hover_color="#ff4444",
            text_color="white", corner_radius=4, border_width=0,
            font=ctk.CTkFont(size=11), cursor="hand2"
        )
        rm_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-3, y=3)

        # Название файла под превью
        display_name = path.name if len(path.name) <= 24 else path.stem[:20] + '…'
        name_label = ctk.CTkLabel(
            tile, text=display_name,
            font=ctk.CTkFont(size=11),
            anchor="center", wraplength=self._TILE_W - 10,
            text_color="#cccccc", cursor="hand2"
        )
        name_label.pack(padx=4, pady=(2, 8), fill="x")
        name_label.bind("<Button-1>", lambda e: on_click())

        self.video_widgets[video_path] = tile

    def _reflow_tiles(self):
        """Перекомпоновывает плитки в grid-сетку с динамическим числом колонок."""
        if self.view_mode_var.get() != "👁️ Превью":
            return
        tiles = [t for t in self.video_widgets.values() if t.winfo_exists()]
        if not tiles:
            return

        # Вычисляем число колонок по доступной ширине
        avail_w = self.videos_scroll.winfo_width() - 20  # отступы
        tile_step = self._TILE_W + self._TILE_GAP * 2
        cols = max(1, avail_w // tile_step)

        # Сбрасываем геометрию для всех плиток
        for tile in tiles:
            tile.grid_forget()

        # Настраиваем колонки с равным весом
        for c in range(cols):
            self.videos_scroll.grid_columnconfigure(c, weight=1, uniform="tile")
        for i, tile in enumerate(tiles):
            row = i // cols
            col = i % cols
            tile.grid(row=row, column=col, padx=self._TILE_GAP // 2,
                      pady=self._TILE_GAP // 2, sticky="nsew")

    def _on_scroll_configure(self, event):
        """Перекомпоновка плиток при изменении размера."""
        if self.view_mode_var.get() == "👁️ Превью":
            self._reflow_tiles()

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
            return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(self._TILE_W, self._TILE_H))
        else:
            placeholder = Image.new('RGB', (self._TILE_W, self._TILE_H), color='#3a3a3a')
            return ctk.CTkImage(light_image=placeholder, dark_image=placeholder, size=(self._TILE_W, self._TILE_H))

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
        # После добавления всех элементов перекомпоновываем плитки (для preview)
        if value == "👁️ Превью":
            self.update_idletasks()
            self._reflow_tiles()
    
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
        self._reflow_tiles()

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
        self._reflow_tiles()

    def _on_model_changed(self, choice: str):
        """Показывает/скрывает Azure-поля при выборе модели."""
        if choice == "azure":
            self.azure_frame.pack(fill="x", padx=8, pady=(0,2))
            self.gpu_var.set(False)  # Azure не требует GPU
        else:
            self.azure_frame.pack_forget()

    def _toggle_advanced(self):
        """Разворачивает/сворачивает панель доп.настроек."""
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.pack(fill="x", after=self.bar_row)
            self.btn_advanced.configure(text="⚙ Дополнительно ▲")
        else:
            self.advanced_frame.pack_forget()
            self.btn_advanced.configure(text="⚙ Дополнительно ▼")

    def update_progress(self, fraction: float):
        """Обновляет прогресс-бар (fraction 0.0–1.0) и метку с % + примерным временем."""
        fraction = min(1.0, max(0.0, fraction))
        self.progress_bar.set(fraction)
        pct = int(fraction * 100)
        if pct > 0 and self._start_time:
            elapsed = time.time() - self._start_time
            if elapsed > 2:
                eta = elapsed / fraction * (1 - fraction)  # секунд до конца
                if eta >= 120:
                    label = f"{pct}%  ~{int(eta // 60)} мин"
                elif eta >= 60:
                    label = f"{pct}%  ~1 мин"
                else:
                    label = f"{pct}%  ~{int(eta)} с"
            else:
                label = f"{pct}%  --:--"
        else:
            label = f"{pct}%  --:--"
        self.progress_label.configure(text=label)

    def start_processing(self):
        if not self.files_to_process or self.is_running:
            return
        self.is_running = True
        self.progress_bar.set(0)
        self.progress_label.configure(text="0%  --:--")
        self._start_time = time.time()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_select_files.configure(state="disabled")
        self.btn_select_folder.configure(state="disabled")
        self.engine = SubtitleEngine(
            model_size=self.model_var.get(),
            use_gpu=self.gpu_var.get(),
            log_callback=self.log_message,
            azure_key=self.azure_key_var.get().strip(),
            azure_region=self.azure_region_var.get().strip()
        )
        threading.Thread(target=self._process_thread, daemon=True).start()

    def _process_thread(self):
        try:
            self.engine.process_videos(self.files_to_process, self.overwrite_var.get(),
                                       progress_callback=lambda f: self.after(0, self.update_progress, f))
        except Exception as e:
            self.log_message(f"Ошибка: {e}")
        finally:
            self.after(0, self._on_finished)

    def _on_finished(self):
        self.is_running = False
        self.progress_bar.set(1)
        self.progress_label.configure(text="100%  ✅ Готово")
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
        self._reflow_tiles()

    def stop_processing(self):
        if self.engine and self.is_running:
            self.log_message("Остановка...")
            self.engine.stop()

if __name__ == "__main__":
    App().mainloop()