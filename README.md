# SubtitleMaker 🎬

Десктопное приложение для автоматической генерации и перевода субтитров из видеофайлов.

## Возможности

- 🌍 **Автоопределение языка** аудио (русский, английский и другие)
- 📝 Генерация субтитров на **двух языках одновременно** (RU + EN)
- 🖥️ Удобный GUI на CustomTkinter (тёмная тема)
- ⚙️ Выбор размера модели Whisper (tiny → large-v3)
- 🛑 Возможность отмены обработки в любой момент
- 📦 Встроенный FFmpeg (через imageio-ffmpeg)

## Установка

```bash
pip install -r requirements.txt
python main.py
```

> При первом запуске модель скачается автоматически в папку `models/`.

## Как это работает

1. Выберите видеофайлы или папку с видео
2. Выберите размер модели (рекомендуется `small` для баланса скорости/качества)
3. Нажмите «Старт»
4. Получите два SRT-файла рядом с каждым видео: `_ru.srt` и `_en.srt`

## Стек

- Python 3.11+
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — транскрибация
- [deep-translator](https://github.com/nidhaloff/deep-translator) — перевод через Google Translate
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — GUI
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) — встроенный FFmpeg

## Лицензия

MIT
