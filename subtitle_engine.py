import time
from pathlib import Path
import os
import sys
import re
import json
import hashlib
import imageio_ffmpeg
import subprocess

os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

SUBTITLES_DIR = Path(__file__).parent / "subtitles"
SUBTITLES_DIR.mkdir(exist_ok=True)

MAPPING_PATH = SUBTITLES_DIR / "mapping.json"

from deep_translator import GoogleTranslator


def _normalize_path(video_path: str) -> str:
    """Приводит путь к каноническому виду: resolve() + normpath.
    Гарантирует, что один и тот же файл всегда даёт одинаковый хеш."""
    try:
        return str(Path(video_path).resolve())
    except Exception:
        return str(Path(video_path))


def _path_hash(video_path: str) -> str:
    """MD5-хеш от нормализованного пути."""
    return hashlib.md5(_normalize_path(video_path).encode()).hexdigest()


def _load_mapping() -> dict:
    """Загружает mapping.json: {video_name: hash}."""
    if MAPPING_PATH.exists():
        try:
            with open(MAPPING_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_mapping(mapping: dict):
    """Сохраняет mapping.json."""
    try:
        with open(MAPPING_PATH, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_srt_path(video_path: str, lang: str) -> Path:
    """Возвращает путь к SRT-файлу внутри subtitles/ по хешу пути к видео.
    Сначала ищет по хешу нормализованного пути, затем — по имени файла
    через mapping.json (на случай, если видео было перемещено)."""
    video_hash = _path_hash(video_path)
    srt = SUBTITLES_DIR / f"{video_hash}_{lang}.srt"
    if srt.exists():
        return srt

    # Резервный поиск: по имени файла в mapping.json
    video_name = Path(video_path).name
    mapping = _load_mapping()
    mapped_hash = mapping.get(video_name, "")
    if mapped_hash:
        alt = SUBTITLES_DIR / f"{mapped_hash}_{lang}.srt"
        if alt.exists():
            return alt

    return srt  # возвращаем путь по хешу (даже если файла пока нет — для сохранения)


def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis -= 1000
        secs += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def translate_text(text: str, source='ru', target='en', retries=3) -> str:
    translator = GoogleTranslator(source=source, target=target)
    for attempt in range(retries):
        try:
            result = translator.translate(text)
            return result if result else text
        except Exception as e:
            if attempt == retries - 1:
                return f"[Translation Error: {e}]"
            time.sleep(2)
    return text


def check_deepgram_balance(api_key: str) -> str | None:
    """Проверяет остаток средств на Deepgram-ключе.
    Возвращает:
      - "$X.XX USD"  — баланс успешно получен
      - "free_tier"   — бесплатный ключ без доступа к биллингу (403)
      - None          — ошибка сети или невалидный ключ"""
    import urllib.request
    import urllib.error
    try:
        # Получаем project_id
        req = urllib.request.Request(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        projects = data.get("projects", [])
        if not projects:
            return None
        project_id = projects[0]["project_id"]

        # Получаем баланс
        req2 = urllib.request.Request(
            f"https://api.deepgram.com/v1/projects/{project_id}/balances",
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req2, timeout=10) as resp:
            data2 = json.loads(resp.read())
        balances = data2.get("balances", [])
        if balances:
            b = balances[0]
            amount = b.get("amount", 0)
            unit = b.get("units", "USD")
            return f"${amount:.2f} {unit}"
        return "$0.00"
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return "free_tier"  # бесплатный ключ — нет доступа к биллингу
        return None
    except Exception:
        return None


class SubtitleEngine:
    def __init__(self, model_size="small", use_gpu=True, log_callback=None,
                 deepgram_keys=None, highlight_callback=None,
                 split_sentences=False):
        self.model_size = model_size
        self.use_gpu = use_gpu
        self.log = log_callback if log_callback else print
        # Поддержка мульти-ключей: список ключей (один на строку)
        self.deepgram_keys = [k.strip() for k in (deepgram_keys or []) if k.strip()]
        self._dg_key_index = 0  # индекс текущего ключа для ротации
        self.highlight_callback = highlight_callback
        self.split_sentences = split_sentences
        self.model = None
        self.stop_flag = False
        self._gpu_failed = False   # запоминаем, что GPU однажды упал

    def check_stop(self):
        if self.stop_flag:
            raise InterruptedError("Cancelled by user.")

    # --- Whisper ---

    def _transcribe_whisper(self, video_path, progress_callback, idx, total):
        from faster_whisper import WhisperModel

        try:
            device, ct = "cpu", "int8"
            if self.use_gpu and not self._gpu_failed:
                try:
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() > 0:
                        device, ct = "cuda", "int8_float16"
                        self.log("GPU detected. Using GPU (int8_float16).")
                    else:
                        self.log("No GPU found. Using CPU.")
                except Exception as e:
                    self.log(f"GPU unavailable ({e}). CPU.")

            self.log(f"Loading model '{self.model_size}' on {device.upper()}...")
            self.model = WhisperModel(self.model_size, device=device,
                                      compute_type=ct, download_root="models")
            self.log("Model loaded!")
        except Exception as e:
            self.log(f"Error loading model: {e}")
            raise

        def _report(seg_progress):
            if progress_callback:
                progress_callback((idx - 1 + seg_progress) / total)

        try:
            segments, info = self.model.transcribe(
                str(video_path), beam_size=3, vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                task="transcribe"
            )
        except Exception as e:
            if "cublas" in str(e).lower() or "cuda" in str(e).lower():
                self.log("GPU error, switching to CPU...")
                self._gpu_failed = True  # запоминаем — больше не пробуем GPU
                self.model = WhisperModel(self.model_size, device="cpu",
                                          compute_type="int8", download_root="models")
                segments, info = self.model.transcribe(
                    str(video_path), beam_size=3, vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500),
                    task="transcribe"
                )
            else:
                raise

        lang = getattr(info, 'language', None) or 'unknown'
        self.log(f"Audio language: {lang}")
        return segments, lang, _report

    # --- Deepgram ---

    def _ensure_deepgram_sdk(self):
        """Пытается импортировать Deepgram-клиент, при неудаче — автоустановка."""
        try:
            from deepgram import DeepgramClient
            return DeepgramClient
        except ImportError:
            self.log("Deepgram SDK not found. Installing deepgram-sdk...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "deepgram-sdk"],
                capture_output=True, timeout=120
            )
            if result.returncode != 0:
                err = result.stderr.decode()[:200]
                self.log(f"Install failed: {err}")
                self.log("Please run manually: pip install deepgram-sdk")
                raise RuntimeError("deepgram-sdk install failed")
            import importlib
            importlib.invalidate_caches()
            sys.modules.pop('deepgram', None)
            from deepgram import DeepgramClient
            self.log("Deepgram SDK installed successfully!")
            return DeepgramClient

    def _transcribe_deepgram(self, video_path, progress_callback, idx, total):
        def _report(seg_progress):
            if progress_callback:
                progress_callback((idx - 1 + seg_progress) / total)

        DeepgramClient = self._ensure_deepgram_sdk()

        if not self.deepgram_keys:
            self.log("Deepgram API Key missing. Open Advanced and enter keys.")
            raise ValueError("Deepgram API keys are required")

        # Конвертируем видео в WAV (16kHz, mono)
        wav_path = video_path.with_suffix('.wav')
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.log("Converting to WAV (16kHz, mono)...")
        ffmpeg_result = subprocess.run(
            [ffmpeg, '-i', str(video_path), '-ar', '16000', '-ac', '1',
             '-sample_fmt', 's16', '-y', str(wav_path)],
            capture_output=True, timeout=300
        )
        if ffmpeg_result.returncode != 0:
            stderr_tail = ffmpeg_result.stderr.decode(errors='replace')[-200:]
            self.log(f"FFmpeg error: {stderr_tail}")
            if "does not contain any stream" in stderr_tail:
                raise RuntimeError("No audio track found in video file")
            raise RuntimeError("Audio conversion failed")
        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise RuntimeError("Converted WAV is empty — video may have no audio track")
        _report(0.05)

        with open(wav_path, "rb") as f:
            buffer_data = f.read()

        # Пробуем ключи по очереди (ротация)
        errors = []
        response = None
        try:
            for attempt in range(len(self.deepgram_keys)):
                key_idx = (self._dg_key_index + attempt) % len(self.deepgram_keys)
                key = self.deepgram_keys[key_idx]
                masked = key[:6] + "..." if len(key) > 6 else "***"

                try:
                    client = DeepgramClient(api_key=key)
                    self.log(f"Sending to Deepgram Nova-3 (key {attempt+1}/{len(self.deepgram_keys)}: {masked})...")
                    response = client.listen.v1.media.transcribe_file(
                        request=buffer_data,
                        model="nova-3",
                        smart_format=True,
                        utterances=True,
                        detect_language=True,
                    )
                    # Успех — сдвигаем указатель на следующий ключ
                    self._dg_key_index = (key_idx + 1) % len(self.deepgram_keys)
                    break
                except Exception as e:
                    err_msg = str(e)[:150]
                    status = getattr(e, 'status_code', 0) or getattr(e, 'status', 0)
                    self.log(f"Key {masked}: {status} {err_msg}")
                    errors.append((masked, status, err_msg))
                    # Только на явной ошибке авторизации (401) не пробуем другие ключи
                    # для этого же проекта — но пробуем другие ключи (другие проекты)
                    # На всё остальное (429, 500, таймауты) — пробуем следующий ключ
            else:
                # Все ключи исчерпаны — показываем сводку ошибок
                self.log(f"All {len(self.deepgram_keys)} keys failed:")
                for mk, st, msg in errors:
                    self.log(f"  {mk}: {st} — {msg[:80]}")
                raise RuntimeError(f"All {len(self.deepgram_keys)} keys exhausted.")
        finally:
            # Всегда удаляем WAV
            try:
                wav_path.unlink()
            except Exception:
                pass

        if response is None:
            raise RuntimeError("No response from Deepgram (all keys failed)")

        # Парсим результат (оборачиваем — ошибка структуры ≠ ошибка API)
        try:
            results = response.results
            channel = results.channels[0]
            detected_lang = channel.detected_language or ''

            # Определяем язык
            if 'ru' in detected_lang.lower():
                lang = 'ru'
            elif 'en' in detected_lang.lower():
                lang = 'en'
            else:
                lang = 'unknown'
            self.log(f"Detected language: {lang}")
        except (AttributeError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected Deepgram response structure: {e}")

        class DgSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        def _split_by_sentences(segments, split_enabled):
            """Разбивает длинные сегменты на отдельные предложения.
            Тайминги распределяются пропорционально длине предложений."""
            if not split_enabled:
                return segments
            result = []
            for seg in segments:
                text = seg.text.strip()
                if not text:
                    continue
                # Ищем границы предложений: (.!?) с последующим пробелом или концом строки
                sentences = re.split(r'(?<=[.!?])\s+', text)
                if len(sentences) <= 1:
                    result.append(seg)
                    continue
                # Распределяем тайминги пропорционально длине
                total_chars = sum(len(s) for s in sentences)
                if total_chars == 0:
                    result.append(seg)
                    continue
                duration = seg.end - seg.start
                t = seg.start
                for s_text in sentences:
                    s_text = s_text.strip()
                    if not s_text:
                        continue
                    char_ratio = len(s_text) / total_chars
                    seg_dur = max(0.5, duration * char_ratio)
                    result.append(DgSegment(t, t + seg_dur, s_text))
                    t += seg_dur
            return result

        segments = []
        try:
            # Используем utterances (если есть) или слова
            if results.utterances:
                for utt in results.utterances:
                    text = (utt.transcript or '').strip()
                    if text:
                        segments.append(DgSegment(utt.start, utt.end, text))
                self.log(f"Got {len(segments)} utterances from Deepgram")
            else:
                # Собираем из слов группами по ~10 слов
                alt = channel.alternatives[0] if channel.alternatives else None
                if not alt or not alt.words:
                    self.log("Deepgram returned no words — no speech detected, skipping.")
                    return [], lang, _report
                words = alt.words
                gs, gt = None, []
                for w in words:
                    if gs is None:
                        gs = w.start
                    gt.append(w.word)
                    if len(gt) >= 10:
                        segments.append(DgSegment(gs, w.end, ' '.join(gt)))
                        gs, gt = None, []
                if gt:
                    segments.append(DgSegment(gs, words[-1].end, ' '.join(gt)))
                self.log(f"Got {len(segments)} word-groups from Deepgram")

            # Дробим длинные сегменты по предложениям (если включено)
            segments = _split_by_sentences(segments, self.split_sentences)
            if self.split_sentences:
                self.log(f"After sentence split: {len(segments)} segments")

            if not segments:
                self.log("Deepgram returned no results — no speech detected, skipping.")
                return [], lang, _report
        except (AttributeError, IndexError, TypeError) as e:
            raise RuntimeError(f"Failed to extract segments from Deepgram response: {e}")

        return segments, lang, _report

    # --- Main loop ---

    def process_videos(self, video_paths, overwrite=False, progress_callback=None):
        is_deepgram = self.model_size == "deepgram"
        total = len(video_paths)

        for idx, p in enumerate(video_paths, start=1):
            try:
                self.check_stop()
                video_path = Path(p)
                srt_ru = get_srt_path(str(video_path), "ru")
                srt_en = get_srt_path(str(video_path), "en")

                if not overwrite and srt_ru.exists() and srt_en.exists():
                    self.log(f"Skip {video_path.name} (SRT already exist).")
                    if progress_callback:
                        progress_callback(idx / total)
                    continue

                self.log(f"\nProcessing: {video_path.name}...")
                if self.highlight_callback:
                    self.highlight_callback(str(video_path))

                if is_deepgram:
                    segments, lang, _report = self._transcribe_deepgram(
                        video_path, progress_callback, idx, total)
                else:
                    segments, lang, _report = self._transcribe_whisper(
                        video_path, progress_callback, idx, total)

                if not segments:
                    self.log(f"{video_path.name}: нет речи, создаём пустые SRT.")
                    # Всё равно создаём пустые SRT-файлы, чтобы UI не показывал «не найден»
                    for srt in (srt_ru, srt_en):
                        with open(srt, "w", encoding="utf-8") as f:
                            f.write("")
                    mapping = _load_mapping()
                    mapping[video_path.name] = _path_hash(str(video_path))
                    _save_mapping(mapping)
                    if progress_callback:
                        progress_callback(idx / total)
                    continue

                ru_lines, en_lines = [], []

                for i, seg in enumerate(segments, start=1):
                    self.check_stop()
                    ts = f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}"
                    text = seg.text.strip()
                    if not text:
                        continue

                    if lang == 'ru':
                        ru, en = text, translate_text(text, 'ru', 'en')
                    elif lang == 'en':
                        en, ru = text, translate_text(text, 'en', 'ru')
                    else:
                        en = translate_text(text, 'auto', 'en')
                        ru = translate_text(text, 'auto', 'ru')

                    ru_lines.append(f"{i}\n{ts}\n{ru}\n")
                    en_lines.append(f"{i}\n{ts}\n{en}\n")

                    if i % 3 == 0:
                        _report(i / (i + 30))

                self.check_stop()
                with open(srt_ru, "w", encoding="utf-8") as f:
                    f.write("\n".join(ru_lines))
                with open(srt_en, "w", encoding="utf-8") as f:
                    f.write("\n".join(en_lines))
                # Сохраняем mapping: имя файла -> хеш (для поиска при перемещении видео)
                mapping = _load_mapping()
                mapping[video_path.name] = _path_hash(str(video_path))
                _save_mapping(mapping)
                self.log(f"Saved: {srt_ru.name} and {srt_en.name}.")
                _report(1.0)

            except InterruptedError as ie:
                self.log(str(ie))
                break
            except Exception as e:
                self.log(f"Error processing {Path(p).name}: {e}")
                # Создаём пустые SRT, чтобы UI не показывал «не найден»
                for srt in (srt_ru, srt_en):
                    with open(srt, "w", encoding="utf-8") as f:
                        f.write("")
                mapping = _load_mapping()
                mapping[video_path.name] = _path_hash(str(video_path))
                _save_mapping(mapping)
                if progress_callback:
                    progress_callback(idx / total)

        self.log("Done!")

    def stop(self):
        self.stop_flag = True
