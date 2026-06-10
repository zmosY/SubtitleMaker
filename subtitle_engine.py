import time
from pathlib import Path
import os
import sys
import imageio_ffmpeg
import subprocess

os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

from deep_translator import GoogleTranslator


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


class SubtitleEngine:
    def __init__(self, model_size="small", use_gpu=True, log_callback=None,
                 deepgram_key=""):
        self.model_size = model_size
        self.use_gpu = use_gpu
        self.log = log_callback if log_callback else print
        self.deepgram_key = deepgram_key
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

        if not self.deepgram_key:
            self.log("Deepgram API Key missing. Open Advanced and enter the key.")
            raise ValueError("Deepgram API key is required")

        # Конвертируем видео в WAV (16kHz, mono)
        wav_path = video_path.with_suffix('.wav')
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.log("Converting to WAV (16kHz, mono)...")
        subprocess.run(
            [ffmpeg, '-i', str(video_path), '-ar', '16000', '-ac', '1',
             '-sample_fmt', 's16', '-y', str(wav_path)],
            capture_output=True, timeout=300
        )
        _report(0.05)

        client = DeepgramClient(api_key=self.deepgram_key)

        with open(wav_path, "rb") as f:
            buffer_data = f.read()

        self.log("Sending to Deepgram Nova-3...")
        response = client.listen.v1.media.transcribe_file(
            request=buffer_data,
            model="nova-3",
            smart_format=True,
            utterances=True,
            detect_language=True,
        )

        # Удаляем WAV
        try:
            wav_path.unlink()
        except Exception:
            pass

        # Парсим результат
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

        class DgSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        segments = []
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
                self.log("Deepgram returned no words.")
                raise RuntimeError("No transcription from Deepgram")
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

        if not segments:
            self.log("Deepgram returned no results.")
            raise RuntimeError("No transcription from Deepgram")

        return segments, lang, _report

    # --- Main loop ---

    def process_videos(self, video_paths, overwrite=False, progress_callback=None):
        is_deepgram = self.model_size == "deepgram"
        total = len(video_paths)

        for idx, p in enumerate(video_paths, start=1):
            try:
                self.check_stop()
                video_path = Path(p)
                srt_ru = video_path.with_name(f"{video_path.stem}_ru.srt")
                srt_en = video_path.with_name(f"{video_path.stem}_en.srt")

                if not overwrite and srt_ru.exists() and srt_en.exists():
                    self.log(f"Skip {video_path.name} (SRT already exist).")
                    if progress_callback:
                        progress_callback(idx / total)
                    continue

                self.log(f"\nProcessing: {video_path.name}...")

                if is_deepgram:
                    segments, lang, _report = self._transcribe_deepgram(
                        video_path, progress_callback, idx, total)
                else:
                    segments, lang, _report = self._transcribe_whisper(
                        video_path, progress_callback, idx, total)
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
                self.log(f"Saved: {srt_ru.name} and {srt_en.name}.")
                _report(1.0)

            except InterruptedError as ie:
                self.log(str(ie))
                break
            except Exception as e:
                self.log(f"Error processing {Path(p).name}: {e}")

        self.log("Done!")

    def stop(self):
        self.stop_flag = True
