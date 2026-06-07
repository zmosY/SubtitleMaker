import time
from pathlib import Path
import os
import imageio_ffmpeg

# Автоматически добавляем встроенный ffmpeg в системный PATH для текущего процесса
os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

from faster_whisper import WhisperModel
from deep_translator import GoogleTranslator

def format_timestamp(seconds: float) -> str:
    """Форматирует секунды в формат SRT (HH:MM:SS,mmm)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    # Убеждаемcя, что миллисекунды не выходят за 999
    if millis >= 1000:
        millis -= 1000
        secs += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def translate_text(text: str, source: str = 'ru', target: str = 'en', retries: int = 3) -> str:
    """Переводит текст между указанными языками с обработкой ошибок (до 3 попыток)."""
    translator = GoogleTranslator(source=source, target=target)
    for attempt in range(retries):
        try:
            result = translator.translate(text)
            return result if result else text
        except Exception as e:
            if attempt == retries - 1:
                return f"[Translation Error: {e}]"
            time.sleep(2) # Пауза перед следующей попыткой
    return text

class SubtitleEngine:
    """Класс, инкапсулирующий логику генерации и перевода субтитров."""
    def __init__(self, model_size="small", use_gpu=True, log_callback=None):
        self.model_size = model_size
        self.use_gpu = use_gpu
        self.log = log_callback if log_callback else print
        self.model = None
        self.stop_flag = False

    def check_stop(self):
        if self.stop_flag:
            raise InterruptedError("Процесс отменен пользователем.")

    def process_videos(self, video_paths: list, overwrite: bool = False):
        try:
            # Определяем устройство: GPU или CPU
            device = "cpu"
            compute_type = "int8"
            
            if self.use_gpu:
                try:
                    import ctranslate2
                    cuda_devices = ctranslate2.get_cuda_device_count()
                    if cuda_devices > 0:
                        device = "cuda"
                        compute_type = "float16"  # На GPU лучше использовать float16 для скорости
                        self.log(f"Обнаружено {cuda_devices} GPU. Используем видеокарту для ускорения.")
                    else:
                        self.log("Видеокарта не обнаружена. Используем процессор.")
                except Exception as e:
                    self.log(f"Не удалось инициализировать GPU ({e}). Переключаемся на процессор.")
                    device = "cpu"
                    compute_type = "int8"
            else:
                self.log("Использование GPU отключено в настройках. Работаем на процессоре.")
            
            self.log(f"Загрузка модели '{self.model_size}' на {device.upper()} (при первом запуске скачивается из интернета. Ждите...)...")
            self.model = WhisperModel(
                self.model_size, 
                device=device, 
                compute_type=compute_type,
                download_root="models"
            )
            self.log("Модель успешно загружена!")
        except Exception as e:
            self.log(f"Ошибка загрузки модели: {e}")
            return

        for p in video_paths:
            try:
                self.check_stop()
                video_path = Path(p)
                srt_ru = video_path.with_name(f"{video_path.stem}_ru.srt")
                srt_en = video_path.with_name(f"{video_path.stem}_en.srt")

                if not overwrite and srt_ru.exists() and srt_en.exists():
                    self.log(f"--> Пропуск {video_path.name} (субтитры уже существуют).")
                    continue

                self.log(f"\n--> Обработка: {video_path.name}...")
                
                # Транскрибация (автоопределение языка)
                segments, info = self.model.transcribe(
                    str(video_path), 
                    beam_size=3,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500),
                    task="transcribe"
                )
                detected_lang = getattr(info, 'language', None) or 'unknown'
                self.log(f"Обнаружен язык аудио: {detected_lang}")
                
                ru_lines = []
                en_lines = []
                
                # Итерация через сегменты (генератор)
                for i, segment in enumerate(segments, start=1):
                    self.check_stop()
                    
                    start_str = format_timestamp(segment.start)
                    end_str = format_timestamp(segment.end)
                    time_block = f"{start_str} --> {end_str}"
                    
                    original_text = segment.text.strip()
                    if not original_text:
                        continue
                        
                    # Генерируем обе версии в зависимости от языка оригинала
                    if detected_lang == 'ru':
                        text_ru = original_text
                        text_en = translate_text(original_text, source='ru', target='en')
                    elif detected_lang == 'en':
                        text_en = original_text
                        text_ru = translate_text(original_text, source='en', target='ru')
                    else:
                        # Для других языков: переводим на оба целевых языка
                        text_en = translate_text(original_text, source='auto', target='en')
                        text_ru = translate_text(original_text, source='auto', target='ru')
                    
                    ru_lines.append(f"{i}\n{time_block}\n{text_ru}\n")
                    en_lines.append(f"{i}\n{time_block}\n{text_en}\n")
                    
                    # Лог для понимания, что процесс идет
                    if i % 10 == 0:
                        self.log(f"Сгенерировано {i} строк субтитров...")

                self.check_stop()
                
                # Запись обеих файлов субтитров
                with open(srt_ru, "w", encoding="utf-8") as f:
                    f.write("\n".join(ru_lines))
                    
                with open(srt_en, "w", encoding="utf-8") as f:
                    f.write("\n".join(en_lines))
                    
                self.log(f"Успешно сохранено: {srt_ru.name} и {srt_en.name}.")

            except InterruptedError as ie:
                self.log(str(ie))
                break
            except Exception as e:
                self.log(f"Ошибка при обработке {Path(p).name}: {e}")

        self.log("\nОбработка завершена!")

    def stop(self):
        """Остановить процесс (флаг)"""
        self.stop_flag = True