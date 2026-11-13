"""Built-in voices (DESIGN 7c Audio, 18): Piper voices read the tours aloud on this PC, so
the player sounds natural even where the browser has only robot voices (Windows gives apps
no Narrator natural voices).

Piper (`pip install "coderstanding[voices]"`, the `piper-tts` package, GPL) is optional and never
shipped with Coderstanding. Voices come only from a folder the user fills by hand:
`<data home>/voices/` (`voices_dir`), one voice = `<name>.onnx` + `<name>.onnx.json` from Piper's
voice list on Hugging Face. Nothing here downloads anything: the offline guard stays as it is.

`Voices` loads each voice model once and keeps it, and caches every clip it makes on disk by
(voice, voice file, text), so each speech chunk is synthesized once.
"""

import hashlib
import importlib.util
import io
import json
import threading
import wave
from pathlib import Path

from . import apps

MODEL_SUFFIX = ".onnx"
CONFIG_SUFFIX = ".onnx.json"
CACHE_FOLDER = "cache"
CACHE_LIMIT = 2000      # clips kept on disk; the oldest go first
MAX_TEXT = 5000         # characters in one clip (a speech chunk or a line note)
INSTALL_HINT = 'pip install "coderstanding[voices]"'
FOLDER_HINT = "<name>.onnx + <name>.onnx.json from Piper's voice list on Hugging Face"
LICENCE_HINT = ("Each Piper voice has its own licence (see its model card): check it before "
                "using a voice for work.")


def voices_dir():
    """The folder the user drops Piper voices into (follows CODERSTANDING_DATA_HOME)."""
    return apps.data_home() / "voices"


def piper_installed():
    """True when the optional `piper-tts` package can be imported."""
    return importlib.util.find_spec("piper") is not None


def piper_lang(code):
    """Piper's language code to a browser one: `en_US` -> `en-US`."""
    return str(code or "").replace("_", "-")


def read_voice(config_path):
    """{name, lang, quality} of one voice from its `.onnx.json`, or None when the pair is
    incomplete or the config is not Piper's."""
    name = config_path.name[:-len(CONFIG_SUFFIX)]
    if not (config_path.parent / f"{name}{MODEL_SUFFIX}").is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(config, dict):
        return None
    lang = section(config, "language").get("code") or section(config, "espeak").get("voice")
    return {"name": name, "lang": piper_lang(lang), "quality": section(config, "audio").get("quality") or ""}


def section(config, key):
    """A sub-object of a voice config, {} when missing or not an object."""
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def list_voices(folder):
    """Every complete voice in `folder`, by name."""
    if not folder.is_dir():
        return []
    found = (read_voice(path) for path in sorted(folder.glob(f"*{CONFIG_SUFFIX}")))
    return [voice for voice in found if voice]


def load_piper_voice(model_path):
    """The real loader: a PiperVoice (piper-tts 1.3 or newer) for one `.onnx` file."""
    from piper import PiperVoice
    return PiperVoice.load(str(model_path))


def wav_bytes(model, text):
    """One clip as WAV bytes from a loaded model (anything with Piper's `synthesize_wav`)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        model.synthesize_wav(text, wav_file)
    return buffer.getvalue()


class Voices:
    """The built-in voices of this PC: the listing, and text -> WAV with a disk cache.
    `load_voice(model_path)` is the model loader (tests pass a fake), `installed()` says whether
    it can run; both default to the real Piper."""

    def __init__(self, folder=None, load_voice=load_piper_voice, installed=piper_installed):
        self.fixed_folder = Path(folder) if folder else None
        self.load_voice = load_voice
        self.installed = installed
        self.models = {}            # (name, file stamp) -> the loaded model, kept
        self.lock = threading.Lock()  # one synthesis at a time; models load once

    @property
    def folder(self):
        return self.fixed_folder or voices_dir()

    def listing(self):
        """GET /api/voices: the folder, whether Piper is installed, and the voices it can use
        (none while Piper is missing; `found` still counts the voice files)."""
        found = list_voices(self.folder)
        ready = self.installed()
        return {"folder": str(self.folder), "installed": ready, "found": len(found),
                "voices": found if ready else [], "install": INSTALL_HINT, "add": FOLDER_HINT,
                "licence": LICENCE_HINT}

    def summary(self):
        """One line for `coderstanding privacy`."""
        found = len(list_voices(self.folder))
        state = "installed" if self.installed() else f"not installed ({INSTALL_HINT})"
        return (f"Piper ({state}) speaks on this PC; {found} voices in {self.folder} "
                f"(add {FOLDER_HINT})")

    def speak(self, name, text):
        """WAV bytes of `text` in voice `name`: from the cache, else made once and cached."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("no text to speak")
        if len(text) > MAX_TEXT:
            raise ValueError(f"at most {MAX_TEXT} characters per clip")
        model_path = self.model_path(name)
        if not self.installed():
            raise ValueError(f"Piper is not installed: {INSTALL_HINT}")
        stamp = file_stamp(model_path)
        clip = self.cache_path(name, stamp, text)
        if clip.is_file():
            return clip.read_bytes()
        with self.lock:
            data = wav_bytes(self.model(name, model_path, stamp), text)
        self.store(clip, data)
        return data

    # ---------- inside ----------

    def model_path(self, name):
        """The `.onnx` of a listed voice; anything else (a path, an unknown name) is refused."""
        if not any(voice["name"] == name for voice in list_voices(self.folder)):
            raise KeyError(f"no built-in voice named {name!r}")
        return self.folder / f"{name}{MODEL_SUFFIX}"

    def model(self, name, model_path, stamp):
        key = (name, stamp)
        if key not in self.models:
            self.models = {k: v for k, v in self.models.items() if k[0] != name}  # a replaced file
            self.models[key] = self.load_voice(model_path)
        return self.models[key]

    def cache_path(self, name, stamp, text):
        digest = hashlib.sha256(f"{name}\n{stamp}\n{text}".encode()).hexdigest()
        return self.folder / CACHE_FOLDER / f"{digest}.wav"

    def store(self, clip, data):
        """Write a clip (temp file + rename, so a reader never sees half of one), then prune."""
        clip.parent.mkdir(parents=True, exist_ok=True)
        part = clip.with_name(clip.name + ".part")
        part.write_bytes(data)
        part.replace(clip)
        prune_cache(clip.parent, CACHE_LIMIT)


def file_stamp(path):
    """Size and change time of a voice file: a replaced voice gets new clips."""
    info = path.stat()
    return f"{info.st_size}-{info.st_mtime_ns}"


def prune_cache(folder, limit):
    """Keep the newest `limit` clips in `folder`."""
    clips = sorted(folder.glob("*.wav"), key=lambda path: path.stat().st_mtime_ns)
    for old in clips[:max(0, len(clips) - limit)]:
        old.unlink(missing_ok=True)
