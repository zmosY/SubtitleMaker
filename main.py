import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog
# Drag & drop — опционально, без него приложение работает
_DND_AVAILABLE = False
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    pass
import threading
from pathlib import Path
import pyperclip
import hashlib
import subprocess
import os
import time
import math
import json
from PIL import Image
from subtitle_engine import SubtitleEngine, get_srt_path, check_deepgram_balance

# Путь к файлу конфигурации
CONFIG_PATH = Path(__file__).parent / "config.json"

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):   # всегда ctk.CTk, чтобы сохранить тёмную тему
    # --- Цвета прогресс-бара ---
    _PB_BG = "#2a2a2a"       # трек (незаполненная часть)
    _PB_FILL = "#4a90d9"     # заполненная часть
    _PB_PULSE = "#7ab8ff"    # цвет при пульсации (ярче)

    def __init__(self):
        super().__init__()

        # Ручная инициализация DnD на ctk.CTk (не меняем базовый класс)
        if _DND_AVAILABLE:
            TkinterDnD.require(self)   # загружает tkdnd для нашей платформы
            # Используем прямые Tcl-команды (методы DnDWrapper не доступны на Tk)
            self.tk.call('tkdnd::drop_target', 'register', self._w, DND_FILES)
        
        # Windows DPI awareness
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

        self.title("SubtitleMaker by AI")
        self.geometry("900x650")
        self.minsize(800, 600)
        self.files_to_process = []
        self.engine = None
        self.is_running = False
        self.selected_video_path = None
        self.video_widgets = {}
        self.current_processing_path = None
        self._progress_current = 0.0
        self._progress_target = 0.0
        self._progress_anim_id = None
        self._pulse_value = 0.0
        self._pulse_anim_id = None
        self.thumbnails_dir = Path(__file__).parent / "thumbnails"
        self.thumbnails_dir.mkdir(exist_ok=True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # PanedWindow чтобы растягивать панели
        self.pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=5,
                                   sashrelief=tk.RAISED, bg="#3a3a3a")
        self.pane.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=10, pady=10)

        self.left_panel = ctk.CTkFrame(self.pane)
        self.left_panel.grid_rowconfigure(2, weight=1)
        self.left_panel.grid_columnconfigure(0, weight=1)
        self.left_panel.grid_columnconfigure(1, weight=0)  # колонка скроллбара — не растягивается

        # Заголовок с переключателем режима просмотра
        header_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=10, pady=(10,5), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(header_frame, text="📁 Видеофайлы", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        
        self.view_mode_var = ctk.StringVar(value="👁️Превью")
        self.view_toggle = ctk.CTkSegmentedButton(
            header_frame,
            values=["👁️Превью", "📋Список"],
            variable=self.view_mode_var,
            command=self._on_view_mode_changed,
            height=26,
            width=180
        )
        self.view_toggle.grid(row=0, column=1, padx=(10,0))
        
        self.btn_clear = ctk.CTkButton(
            header_frame, text="🗑 Очистить", command=self._clear_list,
            width=90, height=26, fg_color="#555555", hover_color="#777777",
            font=ctk.CTkFont(size=12), cursor="hand2", state="disabled"
        )
        self.btn_clear.grid(row=0, column=2, padx=(8,0))
        
        btn_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=10, pady=(0,5), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        self.btn_select_videos = ctk.CTkButton(btn_frame, text="📂 Выбрать видео...", command=self.select_files, height=32)
        self.btn_select_videos.grid(row=0, column=0, padx=0, pady=5, sticky="ew")

        # Свой scrollable-контейнер: Canvas + CTkFrame + CTkScrollbar
        self.videos_canvas = tk.Canvas(self.left_panel, highlightthickness=0,
                                       bg="#2b2b2b")
        self.videos_canvas.grid(row=2, column=0, padx=10, pady=(5,10), sticky="nsew")

        self.videos_inner = ctk.CTkFrame(self.videos_canvas, fg_color="transparent")
        self.videos_inner.grid_columnconfigure(0, weight=1)

        self._canvas_window = self.videos_canvas.create_window(
            (0, 0), window=self.videos_inner, anchor="nw", tags="inner"
        )

        self.videos_scrollbar = ctk.CTkScrollbar(
            self.left_panel, command=self.videos_canvas.yview
        )
        self.videos_canvas.configure(yscrollcommand=self.videos_scrollbar.set)
        # Скроллбар скрыт по умолчанию (покажется, когда контент не влезает)

        # Прокрутка колёсиком мыши — только когда курсор над списком видео
        def _on_mousewheel(event):
            w = self.winfo_containing(event.x_root, event.y_root)
            if w and (w == self.videos_canvas or str(w).startswith(str(self.videos_inner))):
                self.videos_canvas.yview_scroll(-1 * (event.delta // 120), "units")
        self.bind_all("<MouseWheel>", _on_mousewheel, add="+")

        self.videos_canvas.bind("<Configure>", self._on_canvas_configure)

        if _DND_AVAILABLE:
            # Drag & drop hint (видна когда нет видео)
            self.drop_hint = ctk.CTkLabel(
                self.left_panel, text="📥 Перетащите видеофайлы или папки сюда",
                font=ctk.CTkFont(size=13), text_color="#666666", anchor="center"
            )
            self.drop_hint.place(relx=0.5, rely=0.55, anchor="center")

            # Привязываем DnD-события через стандартный bind (virtual events работают после require)
            self.bind('<<Drop>>', self._on_drop, add='+')
            self.bind('<<DragEnter>>', self._on_drag_enter, add='+')
            self.bind('<<DragLeave>>', self._on_drag_leave, add='+')

        self.right_panel = ctk.CTkFrame(self.pane)
        self.pane.add(self.left_panel, minsize=200, width=400, stretch="always")
        self.pane.add(self.right_panel, minsize=200, width=400, stretch="always")
        self.right_panel.grid_rowconfigure(2, weight=1)
        self.right_panel.grid_columnconfigure(0, weight=1)

        self.preview_title = ctk.CTkLabel(self.right_panel, text="Субтитры", font=ctk.CTkFont(size=14, weight="bold"))
        self.preview_title.grid(row=0, column=0, padx=10, pady=(10,5), sticky="w")

        lang_frame = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        lang_frame.grid(row=1, column=0, padx=10, pady=(0,5), sticky="ew")
        self.lang_var = ctk.StringVar(value="ru")
        self.lang_btn = ctk.CTkSegmentedButton(lang_frame, values=["RU","EN"], variable=self.lang_var, command=self._on_lang_changed, height=28)
        self.lang_btn.pack(side="left", padx=(0,10))
        self.copy_btn = ctk.CTkButton(lang_frame, text="📋 Копировать", command=self._copy_subtitle, width=100, height=28, fg_color="#28a745")
        self.copy_btn.pack(side="right")

        self.preview_box = ctk.CTkTextbox(self.right_panel, state="disabled", font=ctk.CTkFont(family="Consolas", size=14))
        self.preview_box.grid(row=2, column=0, padx=10, pady=(5,10), sticky="nsew")

        # --- Нижняя панель: прогресс + кнопки + доп.настройки ---
        self.bottom_frame = ctk.CTkFrame(self)
        self.bottom_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0,5))

        # === Верхняя строка: всегда видна ===
        self.bar_row = ctk.CTkFrame(self.bottom_frame, fg_color="transparent")
        self.bar_row.pack(fill="x", padx=6, pady=(6,2))

        # Прогресс-бар на Canvas: трек + заполнение + пульсация
        self.progress_bar = tk.Canvas(self.bar_row, height=24, highlightthickness=0,
                                      bg=self._PB_BG, bd=0)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0,10))
        self.progress_bar.bind("<Configure>", self._draw_progress_bar)
        self._progress_value = 0.0

        # Проценты + время
        self.progress_label = ctk.CTkLabel(self.bar_row, text="0%  --:--",
                                           width=80, font=ctk.CTkFont(size=12),
                                           anchor="center")
        self.progress_label.pack(side="left", padx=(0,12))



        # Кнопка «Дополнительно»
        self.advanced_visible = False
        # Кнопка Старт/Стоп (одна — переключается)
        self.btn_run = ctk.CTkButton(self.bar_row, text="▶ Старт", command=self._toggle_run,
                                      fg_color="#28a745", hover_color="#218838",
                                      width=120, height=38, font=ctk.CTkFont(size=14, weight="bold"))
        self.btn_run.pack(side="left", padx=(0,12))

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
                          values=["tiny", "base", "small", "medium", "large-v3", "deepgram"],
                          width=100, height=26, command=self._on_model_changed)
        self.model_menu.pack(side="left", padx=(0,15))
        self.overwrite_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(settings_row, text="Перезапись", variable=self.overwrite_var,
                        height=26, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0,10))
        self.gpu_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(settings_row, text="GPU", variable=self.gpu_var,
                        height=26, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0,10))
        self.split_sentences_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(settings_row, text="Дробить длинные", variable=self.split_sentences_var,
                        height=26, font=ctk.CTkFont(size=12)).pack(side="left")

        # Deepgram-настройки (показываются только при выборе "deepgram")
        self.deepgram_frame = ctk.CTkFrame(self.advanced_frame, fg_color="transparent")
        # Заголовок
        ctk.CTkLabel(self.deepgram_frame, text="🔑 Deepgram API Keys:",
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=(0,4), pady=(2,0))

        # Фрейм со строками ключей (Entry + баланс + удалить)
        self.keys_rows_frame = ctk.CTkFrame(self.deepgram_frame, fg_color="transparent")
        self.keys_rows_frame.pack(fill="x", pady=(2,0))
        self.key_entries = []       # список Entry-виджетов
        self.key_balance_labels = []  # список баланс-лейблов для каждой строки
        self._balance_check_id = 0  # счётчик для игнорирования устаревших результатов

        # Кнопка добавления ключа
        add_key_row = ctk.CTkFrame(self.deepgram_frame, fg_color="transparent")
        add_key_row.pack(fill="x", pady=(2,4))
        ctk.CTkButton(add_key_row, text="+ Добавить ключ", command=self._add_key_row,
                      width=130, height=24, fg_color="#2563eb",
                      hover_color="#1d4ed8", font=ctk.CTkFont(size=11)).pack(side="left")
        # Лог (консоль)
        self.log_box = ctk.CTkTextbox(self.advanced_frame, height=90,
                                      state="disabled", font=ctk.CTkFont(family="Consolas", size=9))
        self.log_box.pack(fill="x", padx=8, pady=(4,8))

        # Загружаем сохранённые настройки (должны быть после создания UI)
        self._load_config()
        # Автосохранение при изменении любой настройки
        for var in (self.model_var, self.overwrite_var, self.gpu_var,
                    self.split_sentences_var):
            var.trace_add("write", self._auto_save)
        self.lang_var.trace_add("write", self._auto_save)
        self.view_mode_var.trace_add("write", self._auto_save)
        # Автосохранение настроек при закрытии окна
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Заполняем предпросмотр заглушкой при старте
        self._clear_preview()

        self.log_message("Готово! Выберите видео для работы.")

    def _on_drop(self, event):
        """Обработчик Drag & Drop: видеофайлы + папки с видео."""
        self._on_drag_leave(None)
        raw_paths = self.tk.splitlist(event.data)
        count = 0
        for raw in raw_paths:
            p = Path(raw.strip().strip('{').strip('}'))
            if p.is_dir():
                # Папка — рекурсивно ищем все видео
                try:
                    for video_path in p.rglob('*'):
                        if video_path.suffix.lower() in self._VIDEO_EXTS:
                            vp = str(video_path)
                            if vp not in self.files_to_process:
                                self.files_to_process.append(vp)
                                self._add_video_item(vp)
                                count += 1
                except PermissionError:
                    pass  # нет доступа к подпапке — пропускаем
            elif p.suffix.lower() in self._VIDEO_EXTS:
                vp = str(p)
                if vp not in self.files_to_process:
                    self.files_to_process.append(vp)
                    self._add_video_item(vp)
                    count += 1
        if count:
            self.log_message(f"📥 Добавлено перетаскиванием: {count}")
            self._reflow_tiles()

    def _on_drag_enter(self, event):
        """Подсветка при наведении с файлами."""
        self.left_panel.configure(fg_color="#1a2a3d")

    def _on_drag_leave(self, event):
        """Сброс подсветки."""
        self.left_panel.configure(fg_color=("#dbdbdb", "#2b2b2b"))

    def log_message(self, msg):
        self.after(0, lambda: self._append_log(msg))

    def _append_log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    _VIDEO_EXTS = ('.mp4', '.avi', '.mkv', '.mov')

    def _update_drop_hint(self):
        """Показывает/скрывает подсказку о DnD + управляет кнопкой очистки."""
        # Кнопка очистки — обновляется всегда, независимо от DnD
        self.btn_clear.configure(state="normal" if self.files_to_process else "disabled")
        if not _DND_AVAILABLE:
            return
        if self.files_to_process:
            self.drop_hint.place_forget()
        else:
            self.drop_hint.place(relx=0.5, rely=0.55, anchor="center")

    def _update_scrollbar(self):
        """Показывает скроллбар, только если контент не помещается в canvas."""
        def _check():
            try:
                inner_h = self.videos_inner.winfo_reqheight()
                canvas_h = self.videos_canvas.winfo_height()
                if inner_h > canvas_h + 8:
                    self.videos_scrollbar.grid(row=2, column=1, sticky="ns", padx=(0, 10), pady=(5, 10))
                    self.videos_canvas.configure(yscrollcommand=self.videos_scrollbar.set)
                else:
                    self.videos_scrollbar.grid_remove()
                    self.videos_canvas.yview_moveto(0)
            except Exception:
                pass
        self.after(100, _check)

    def _add_video_item(self, video_path: str):
        path = Path(video_path)
        has_srt = get_srt_path(video_path, "ru").exists() or get_srt_path(video_path, "en").exists()
        self._update_drop_hint()
        if self.view_mode_var.get() == "👁️Превью":
            self._add_video_card(video_path, path, has_srt)
        else:
            self._add_video_row(video_path, path, has_srt)
        self._update_scrollbar()

    def _add_video_row(self, video_path: str, path: Path, has_srt: bool):
        """Компактный элемент для режима списка."""
        item = ctk.CTkFrame(self.videos_inner, fg_color="transparent", border_width=1, border_color="#3a3a3a")
        item.grid_columnconfigure(0, weight=1)
        item.pack(fill="x", pady=2, padx=5)
        def on_click():
            self.selected_video_path = video_path
            self.preview_title.configure(text=path.name)
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
            self._update_drop_hint()
            self._update_scrollbar()
        ctk.CTkButton(item, text="✕", command=remove, width=25, height=25, fg_color="#ef4444", hover_color="#dc2626").grid(row=0, column=2, padx=(5,0))
        self.video_widgets[video_path] = item

    _TILE_W = 130   # ширина превью в плитке (3 шт в ряд)
    _TILE_H = 78    # высота превью в плитке
    _TILE_GAP = 6   # зазор между плитками

    def _add_video_card(self, video_path: str, path: Path, has_srt: bool):
        """Плитка с превью для режима предпросмотра (grid-сетка)."""
        tile = ctk.CTkFrame(self.videos_inner, fg_color="#1e1e1e",
                            border_width=1, border_color="#3a3a3a", corner_radius=8)

        def on_click():
            self.selected_video_path = video_path
            self.preview_title.configure(text=path.name)
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
            self._update_drop_hint()
            self._update_scrollbar()
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
        if self.view_mode_var.get() != "👁️Превью":
            return
        tiles = [t for t in self.video_widgets.values() if t.winfo_exists()]
        if not tiles:
            return

        # Вычисляем число колонок по доступной ширине
        avail_w = self.videos_canvas.winfo_width() - 20  # отступы
        tile_step = self._TILE_W + self._TILE_GAP * 2
        cols = max(1, avail_w // tile_step)

        # Сбрасываем геометрию для всех плиток
        for tile in tiles:
            tile.grid_forget()

        # Настраиваем колонки с равным весом
        for c in range(cols):
            self.videos_inner.grid_columnconfigure(c, weight=1, uniform="tile")
        for i, tile in enumerate(tiles):
            row = i // cols
            col = i % cols
            tile.grid(row=row, column=col, padx=self._TILE_GAP // 2,
                      pady=self._TILE_GAP // 2, sticky="nsew")
        self._update_scrollbar()

    def _on_canvas_configure(self, event):
        """Подгоняет ширину внутреннего фрейма под canvas + перекомпоновка плиток."""
        canvas_w = event.width
        self.videos_canvas.itemconfig(self._canvas_window, width=canvas_w)
        self.videos_canvas.configure(scrollregion=self.videos_canvas.bbox("all"))
        self._update_scrollbar()
        if self.view_mode_var.get() == "👁️Превью":
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
                     '-vframes', '1', '-q:v', '3', '-vf', 'scale=260:156',
                     '-y', str(thumb_path)],
                    capture_output=True, timeout=30
                )
                # Если не получилось — пробуем первый кадр (для очень коротких видео)
                if not thumb_path.exists():
                    subprocess.run(
                        [ffmpeg_bin, '-ss', '0', '-i', video_path,
                         '-vframes', '1', '-q:v', '3', '-vf', 'scale=260:156',
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

    def _clear_list(self):
        """Удаляет все видео из списка (не трогает субтитры)."""
        if self.is_running:
            self.log_message("Нельзя очистить список во время обработки.")
            return
        self.files_to_process.clear()
        for w in self.videos_inner.winfo_children():
            w.destroy()
        self.video_widgets.clear()
        self.selected_video_path = None
        self._clear_preview()
        self._update_drop_hint()
        self._update_scrollbar()
        self.log_message("Список видео очищен.")

    def _load_subtitle(self, video_path: str):
        lang = self.lang_var.get()
        srt_file = get_srt_path(video_path, lang)
        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        if srt_file.exists():
            try:
                with open(srt_file, "r", encoding="utf-8") as f:
                    self.preview_box.insert("1.0", f.read())
            except Exception as e:
                self.preview_box.insert("1.0", f"Ошибка: {e}")
        else:
            self.preview_box.insert("1.0", f"Файл не найден:\n{get_srt_path(video_path, lang).name}")
        self.preview_box.configure(state="disabled")

    _EMPTY_PREVIEW_TEXT = (
        "Здесь пока ничего нет \n\n"
        "1. Добавьте видео (кнопка «📂 Выбрать видео…»)\n"
        "2. Нажмите «▶ Старт»\n"
        "3. Субтитры появятся здесь\n\n"
        "Вы также можете кликнуть на видео слева,\n"
        "чтобы посмотреть готовые субтитры."
    )

    def _clear_preview(self):
        self.preview_title.configure(text="Субтитры")
        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", self._EMPTY_PREVIEW_TEXT)
        self.preview_box.configure(state="disabled")

    def _on_view_mode_changed(self, value):
        """Переключает режим отображения списка видео (превью всегда видно)."""
        # Очищаем и пересоздаём элементы списка
        for w in self.videos_inner.winfo_children():
            w.destroy()
        self.video_widgets.clear()
        for v in self.files_to_process:
            self._add_video_item(v)
        self._update_drop_hint()
        # Применяем режим (правая панель всегда остаётся)
        self._apply_view_mode_layout(value)
        # Восстанавливаем подсветку обрабатываемого видео
        if self.current_processing_path and self.is_running:
            self._highlight_video(self.current_processing_path)
        self._update_scrollbar()
    
    def _apply_view_mode_layout(self, mode_value=None):
        """Применяет текущий режим отображения к интерфейсу.
        Правая панель (предпросмотр) всегда видна."""
        if mode_value is None:
            mode_value = self.view_mode_var.get()
        # Убедимся, что правая панель на месте (могла быть скрыта в старой версии)
        try:
            self.pane.add(self.right_panel, after=self.left_panel,
                          minsize=200, stretch="always")
        except tk.TclError:
            pass  # уже добавлена
        if self.selected_video_path:
            self._load_subtitle(self.selected_video_path)
        if mode_value == "👁️Превью":
            self._reflow_tiles()
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
        """Одной кнопкой: если выбран файл — добавляет его, если папка — находит все видео в ней."""
        # Сначала файлы; если пользователь нажмёт «Отмена», предложим выбрать папку
        files = filedialog.askopenfilenames(
            title="Выберите видеофайлы (или нажмите Отмена, чтобы выбрать папку)",
            filetypes=(("Video","*.mp4 *.avi *.mkv *.mov"),("All","*.*"))
        )
        if files:
            count = 0
            for f in files:
                if f not in self.files_to_process:
                    self.files_to_process.append(f)
                    self._add_video_item(f)
                    count += 1
            self.log_message(f"Добавлено видео: {count}")
        else:
            # Пользователь нажал Отмена — предложим выбрать папку
            folder = filedialog.askdirectory(title="Или выберите папку с видео")
            if folder:
                videos = [str(p) for p in Path(folder).iterdir() if p.suffix.lower() in self._VIDEO_EXTS]
                count = 0
                for v in videos:
                    if v not in self.files_to_process:
                        self.files_to_process.append(v)
                        self._add_video_item(v)
                        count += 1
                self.log_message(f"Найдено видео в папке: {count}")
        self._reflow_tiles()
        self._update_scrollbar()

    def _on_model_changed(self, choice: str):
        """Показывает/скрывает Deepgram-поле при выборе модели."""
        if choice == "deepgram":
            self.deepgram_frame.pack(fill="x", padx=8, pady=(0,2))
            self.gpu_var.set(False)  # Deepgram не требует GPU
        else:
            self.deepgram_frame.pack_forget()
            self.gpu_var.set(True)   # Для локальных моделей GPU доступен

    def _toggle_advanced(self):
        """Разворачивает/сворачивает панель доп.настроек."""
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.pack(fill="x", after=self.bar_row)
            self.btn_advanced.configure(text="⚙ Дополнительно ▲")
        else:
            self.advanced_frame.pack_forget()
            self.btn_advanced.configure(text="⚙ Дополнительно ▼")

    def _highlight_video(self, video_path: str):
        """Подсвечивает обрабатываемое видео жёлтой рамкой."""
        self.current_processing_path = video_path
        for path, widget in self.video_widgets.items():
            if not widget.winfo_exists():
                continue
            if path == video_path:
                widget.configure(border_color="#fbbf24", border_width=2)
            else:
                widget.configure(border_color="#3a3a3a", border_width=1)

    # ─── Прогресс-бар на Canvas (трек + заполнение + бегущий блик) ──

    @staticmethod
    def _hex_to_rgb(hex_color: str):
        """Переводит #rrggbb в (r, g, b)."""
        h = hex_color.lstrip('#')
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def _draw_progress_bar(self, event=None):
        """Рисует трек, заполнение и пульсацию на одном canvas."""
        c = self.progress_bar
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 4 or h < 4:
            return

        # Трек (тёмный фон) — простой прямоугольник
        c.create_rectangle(0, 0, w, h, fill=self._PB_BG, outline="", tags="track")

        # Заполненная часть с пульсацией
        fill_w = int(w * self._progress_value)
        if fill_w > 0:
            p = self._pulse_value if (self.is_running and fill_w > 20) else 0.0
            r1, g1, b1 = self._hex_to_rgb(self._PB_FILL)
            r2, g2, b2 = self._hex_to_rgb(self._PB_PULSE)
            rr = int(r1 + (r2 - r1) * p)
            gg = int(g1 + (g2 - g1) * p)
            bb = int(b1 + (b2 - b1) * p)
            pulse_color = f"#{rr:02x}{gg:02x}{bb:02x}"
            c.create_rectangle(0, 0, fill_w, h, fill=pulse_color, outline="", tags="fill")

        # Обводка
        c.create_rectangle(0, 0, w, h, outline="#3a3a3a", tags="border")

    def update_progress(self, fraction: float):
        fraction = min(1.0, max(0.0, fraction))
        self._progress_target = fraction
        if self._progress_anim_id is None:
            self._animate_progress_step()
        # Метку обновляем сразу (без анимации)
        pct = int(fraction * 100)
        if pct > 0 and self._start_time:
            elapsed = time.time() - self._start_time
            if elapsed > 2:
                eta = elapsed / fraction * (1 - fraction) if fraction > 0 else 0
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

    def _animate_progress_step(self):
        """Один шаг анимации прогресс-бара — lerp к цели."""
        current = self._progress_current
        target = self._progress_target
        # lerp с коэффициентом 0.3 — быстро в начале, плавно в конце
        new = current + (target - current) * 0.3
        if abs(target - new) < 0.002:
            new = target
        self._progress_current = new
        self._progress_value = new
        self._draw_progress_bar()
        if abs(new - target) > 0.001:
            self._progress_anim_id = self.after(16, self._animate_progress_step)
        else:
            self._progress_anim_id = None

    # ─── Пульсация прогресс-бара ───────────────────────────────

    def _start_pulse_animation(self):
        """Запускает пульсацию прогресс-бара (плавно с нуля)."""
        self._pulse_value = 0.0
        self._pulse_start = time.time()
        if self._pulse_anim_id is None:
            self._animate_pulse_step()

    def _stop_pulse_animation(self):
        """Останавливает пульсацию и перерисовывает бар."""
        if self._pulse_anim_id is not None:
            self.after_cancel(self._pulse_anim_id)
            self._pulse_anim_id = None
        self._pulse_value = 0.0
        self._draw_progress_bar()

    def _animate_pulse_step(self):
        """Один кадр анимации пульсации — плавное дыхание."""
        if not self.is_running:
            return
        elapsed = time.time() - self._pulse_start
        self._pulse_value = (math.sin(elapsed * 3.0) + 1.0) / 2.0
        self._draw_progress_bar()
        self._pulse_anim_id = self.after(33, self._animate_pulse_step)

    def _get_deepgram_keys(self) -> list:
        """Возвращает список ключей из строк Entry (без пустых)."""
        keys = []
        for entry in self.key_entries:
            if entry.winfo_exists():
                k = entry.get().strip()
                if k:
                    keys.append(k)
        return keys

    def _add_key_row(self, key_value: str = ""):
        """Добавляет строку с полем ключа, балансом и кнопкой удаления."""
        row = ctk.CTkFrame(self.keys_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)

        # Поле ввода ключа
        entry = ctk.CTkEntry(row, font=ctk.CTkFont(family="Consolas", size=10),
                             height=26)
        entry.pack(side="left", fill="x", expand=True, padx=(0,4))
        if key_value:
            entry.insert(0, key_value)
        entry.bind("<KeyRelease>", lambda e: self._on_keys_changed())
        self.key_entries.append(entry)

        # Лейбл баланса (справа от поля)
        bal_lbl = ctk.CTkLabel(row, text="", width=170, anchor="w",
                               font=ctk.CTkFont(size=9))
        bal_lbl.pack(side="left", padx=(2,4))
        self.key_balance_labels.append(bal_lbl)

        # Кнопка удаления
        def remove():
            row.destroy()
            if entry in self.key_entries:
                idx = self.key_entries.index(entry)
                self.key_entries.pop(idx)
                self.key_balance_labels.pop(idx)
            self._on_keys_changed()
        ctk.CTkButton(row, text="✕", command=remove, width=22, height=22,
                      fg_color="#ef4444", hover_color="#dc2626").pack(side="left")

    def _toggle_run(self):
        """Одна кнопка: запускает или останавливает обработку."""
        if self.is_running:
            self.stop_processing()
        else:
            self.start_processing()

    def start_processing(self):
        if not self.files_to_process or self.is_running:
            return
        self.is_running = True
        if self._progress_anim_id is not None:
            self.after_cancel(self._progress_anim_id)
            self._progress_anim_id = None
        self._progress_current = 0.0
        self._progress_target = 0.0
        self._progress_value = 0.0
        self._draw_progress_bar()
        self._start_pulse_animation()
        self.progress_label.configure(text="0%  --:--")
        self._start_time = time.time()
        self.btn_run.configure(text="⏹ Стоп", fg_color="#dc2626", hover_color="#b91c1c")
        self.btn_select_videos.configure(state="disabled")
        self.engine = SubtitleEngine(
            model_size=self.model_var.get(),
            use_gpu=self.gpu_var.get(),
            log_callback=self.log_message,
            deepgram_keys=self._get_deepgram_keys(),
            highlight_callback=lambda p: self.after(0, self._highlight_video, p),
            split_sentences=self.split_sentences_var.get()
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
        self._stop_pulse_animation()
        self._progress_target = 1.0
        if self._progress_anim_id is None:
            self._animate_progress_step()
        self.progress_label.configure(text="100%  ✅ Готово")
        self.current_processing_path = None
        self.btn_run.configure(text="▶ Старт", fg_color="#28a745", hover_color="#218838")
        self.btn_select_videos.configure(state="normal")
        for w in self.videos_inner.winfo_children():
            w.destroy()
        self.video_widgets.clear()
        for v in self.files_to_process:
            self._add_video_item(v)
        self._update_drop_hint()
        if self.selected_video_path:
            self._load_subtitle(self.selected_video_path)
        self._reflow_tiles()

    # ─── Персистентность настроек ─────────────────────────────────
    def _config_data(self) -> dict:
        """Собирает текущие настройки в словарь."""
        return {
            "model_size": self.model_var.get(),
            "use_gpu": self.gpu_var.get(),
            "overwrite": self.overwrite_var.get(),
            "deepgram_keys": self._get_deepgram_keys(),
            "split_sentences": self.split_sentences_var.get(),
            "view_mode": self.view_mode_var.get(),
            "language": self.lang_var.get(),
            "window_geometry": self.geometry(),
        }

    def _save_config(self):
        """Сохраняет настройки в JSON-файл."""
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._config_data(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Config save error: {e}")

    def _load_config(self):
        """Загружает настройки из JSON-файла и применяет их."""
        try:
            if not CONFIG_PATH.exists():
                return
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
        except Exception:
            return

        if data.get("model_size"):
            self.model_var.set(data["model_size"])
        if data.get("use_gpu") is not None:
            self.gpu_var.set(data["use_gpu"])
        if data.get("overwrite") is not None:
            self.overwrite_var.set(data["overwrite"])
        if data.get("deepgram_keys"):
            keys = data["deepgram_keys"]
            if isinstance(keys, list):
                for k in keys:
                    if k.strip():
                        self._add_key_row(k.strip())
            elif isinstance(keys, str) and keys.strip():
                # Обратная совместимость: старый формат с одним ключом
                self._add_key_row(keys.strip())
        if data.get("split_sentences") is not None:
            self.split_sentences_var.set(data["split_sentences"])
        if data.get("view_mode"):
            self.view_mode_var.set(data["view_mode"])
        if data.get("language"):
            self.lang_var.set(data["language"])
        if data.get("window_geometry"):
            self.geometry(data["window_geometry"])

        # После загрузки применяем layout под режим просмотра
        self._on_view_mode_changed(self.view_mode_var.get())
        # Если выбрана deepgram — показываем поле для ключа
        if self.model_var.get() == "deepgram":
            self.deepgram_frame.pack(fill="x", padx=8, pady=(0,2))

        # Автопроверка баланса при старте, если есть сохранённые ключи
        if self._get_deepgram_keys():
            self.after(500, self._auto_check_balances)

    def _on_close(self):
        """Сохраняет настройки и закрывает приложение."""
        self._stop_pulse_animation()
        if self._progress_anim_id is not None:
            self.after_cancel(self._progress_anim_id)
        self._save_config()
        self.destroy()

    # ─── Автосохранение при изменении настроек ──────────────────
    def _auto_save(self, *_):
        self._save_config()

    def _on_keys_changed(self):
        """Автосохранение + авто-проверка баланса с debounce (2 сек)."""
        if hasattr(self, '_keys_save_after_id'):
            self.after_cancel(self._keys_save_after_id)
        self._keys_save_after_id = self.after(2000, self._on_keys_idle)

    def _on_keys_idle(self):
        """Вызывается через 2 сек после последнего изменения ключей."""
        self._auto_save()
        self._auto_check_balances()

    def _auto_check_balances(self):
        """Автоматически проверяет баланс всех ключей в фоне."""
        keys = self._get_deepgram_keys()
        # Инкрементируем счётчик — устаревшие результаты будут проигнорированы
        self._balance_check_id += 1
        check_id = self._balance_check_id
        # Сбрасываем старые лейблы на «проверка...»
        for lbl in self.key_balance_labels:
            if lbl.winfo_exists():
                lbl.configure(text="⏳", text_color="#94a3b8")
        if not keys:
            return

        def _fetch():
            results = []
            for key in keys:
                balance = check_deepgram_balance(key)
                results.append((key, balance))
            try:
                self.after(0, lambda cid=check_id, res=results: self._show_balances_inline(cid, res))
            except Exception:
                pass  # окно закрыто — ничего не делаем

        threading.Thread(target=_fetch, daemon=True).start()

    def _show_balances_inline(self, check_id: int, results):
        """Обновляет баланс-лейблы, сопоставляя по значению ключа."""
        if check_id != self._balance_check_id:
            return  # устаревший результат — игнорируем
        # Строим словарь: ключ -> баланс
        balance_map = {key: bal for key, bal in results}
        # Обновляем лейблы, сопоставляя по текущему значению Entry
        for i, entry in enumerate(self.key_entries):
            if i >= len(self.key_balance_labels):
                break
            lbl = self.key_balance_labels[i]
            if not lbl.winfo_exists() or not entry.winfo_exists():
                continue
            key = entry.get().strip()
            balance = balance_map.get(key)
            if balance == "free_tier":
                lbl.configure(text="🔓 Free tier ($200)", text_color="#60a5fa")
            elif balance:
                try:
                    amt = float(balance.replace("$","").split()[0])
                    color = "#4ade80" if amt > 0 else "#ef4444"
                except ValueError:
                    color = "#4ade80"
                lbl.configure(text=balance, text_color=color)
            elif balance is None and key:
                lbl.configure(text="⚠ ошибка", text_color="#ef4444")
            else:
                lbl.configure(text="", text_color="#94a3b8")

    def stop_processing(self):
        if self.engine and self.is_running:
            self.log_message("Остановка...")
            self._stop_pulse_animation()
            self.engine.stop()

if __name__ == "__main__":
    App().mainloop()