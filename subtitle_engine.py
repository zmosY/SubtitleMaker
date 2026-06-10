import time
from pathlib import Path
import os
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
                 azure_key="", azure_region="eastus"):
        self.model_size = model_size
        self.use_gpu = use_gpu
        self.log = log_callback if log_callback else print
        self.azure_key = azure_key
        self.azure_region = azure_region
        self.model = None
        self.stop_flag = False

    def check_stop(self):
        if self.stop_flag:
            raise InterruptedError("Cancelled by user.")

    # --- Whisper ---

    def _transcribe_whisper(self, video_path, progress_callback, idx, total):
        from faster_whisper import WhisperModel

        try:
            device, ct = "cpu", "int8"
            if self.use_gpu:
                try:
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() > 0:
                        device, ct = "cuda", "float16"
                        self.log("GPU detected. Using GPU.")
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

    # --- Azure Speech ---

    def _transcribe_azure(self, video_path, progress_callback, idx, total):
        import threading

        def _report(seg_progress):
            if progress_callback:
                progress_callback((idx - 1 + seg_progress) / total)

        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            self.log("Azure SDK not installed. Run: pip install azure-cognitiveservices-speech")
            raise

        if not self.azure_key:
            self.log("Azure API Key missing. Open 'Advanced' and enter the key.")
            raise ValueError("Azure API key is required")

        wav_path = video_path.with_suffix('.wav')
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.log("Converting to WAV (16kHz, mono)...")
        subprocess.run(
            [ffmpeg, '-i', str(video_path), '-ar', '16000', '-ac', '1',
             '-sample_fmt', 's16', '-y', str(wav_path)],
            capture_output=True, timeout=300
        )
        _report(0.05)

        speech_config = speechsdk.SpeechConfig(
            subscription=self.azure_key, region=self.azure_region
        )
        auto_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
            languages=["ru-RU", "en-US"]
        )
        audio_config = speechsdk.AudioConfig(filename=str(wav_path))
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
            auto_detect_source_language_config=auto_config
        )

        all_results = []
        done_event = threading.Event()

        def on_recognized(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                all_results.append(evt.result)
                n = len(all_results)
                if n % 5 == 0:
                    _report(0.05 + min(0.85, n / (n + 50)) * 0.90)

        def on_canceled(evt):
            self.log(f"Azure: session cancelled ({evt.result.reason})")
            done_event.set()

        def on_stopped(evt):
            done_event.set()

        recognizer.recognized.connect(on_recognized)
        recognizer.canceled.connect(on_canceled)
        recognizer.session_stopped.connect(on_stopped)

        self.log("Sending to Azure Speech...")
        recognizer.start_continuous_recognition_async().get()
        done_event.wait(timeout=3600)
        recognizer.stop_continuous_recognition_async().get()

        try:
            wav_path.unlink()
        except Exception:
            pass

        if not all_results:
            self.log("Azure returned no results.")
            raise RuntimeError("No results from Azure")

        self.log(f"Got {len(all_results)} segments from Azure")

        class AzSegment:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        segments = []
        for r in all_results:
            start = r.offset / 10_000_000
            end = (r.offset + r.duration) / 10_000_000
            text = r.text.strip()
            if text:
                segments.append(AzSegment(start, end, text))

        return segments, "unknown", _report

    # --- Main loop ---

    def process_videos(self, video_paths, overwrite=False, progress_callback=None):
        is_azure = self.model_size == "azure"
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

                if is_azure:
                    segments, lang, _report = self._transcribe_azure(
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
