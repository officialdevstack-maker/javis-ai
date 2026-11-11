import io
import wave
from functools import lru_cache

from faster_whisper import WhisperModel
from piper import PiperVoice

from .config import get_settings


class VoiceError(Exception):
    """Raised when speech recognition or synthesis cannot complete."""


@lru_cache
def _whisper() -> WhisperModel:
    return WhisperModel(get_settings().whisper_model, device="cpu", compute_type="int8")


@lru_cache
def _voice() -> PiperVoice:
    path = get_settings().resolved_piper_voice_path
    if not path.is_file():
        raise VoiceError(f"Piper voice not found at {path}. See the setup steps for downloading one.")
    return PiperVoice.load(str(path))


def transcribe(audio: bytes) -> str:
    settings = get_settings()
    try:
        segments, _ = _whisper().transcribe(
            io.BytesIO(audio),
            language=settings.whisper_language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:  # PyAV / ctranslate2 raise many unrelated error types
        raise VoiceError(f"Could not transcribe the audio: {exc}") from exc


def synthesize(text: str) -> bytes:
    voice = _voice()
    buffer = io.BytesIO()
    try:
        with wave.open(buffer, "wb") as wav_file:
            synthesize_wav = getattr(voice, "synthesize_wav", None)
            if synthesize_wav is not None:
                synthesize_wav(text, wav_file)
            else:
                voice.synthesize(text, wav_file)
    except Exception as exc:
        raise VoiceError(f"Could not synthesize speech: {exc}") from exc
    return buffer.getvalue()
