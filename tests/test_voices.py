"""Built-in Piper voices (voices.py, DESIGN 7c Audio): the folder, the listing, text -> WAV with
a disk cache, and the server routes. A fake synthesizer stands in for Piper; the one real-Piper
test skips without piper-tts or a voice. Nothing here plays sound: clips are checked as files."""

import io
import json
import os
import shutil
import threading
import wave
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from coderstanding import voices as voices_module
from coderstanding.apps import DATA_HOME_ENV
from coderstanding.hub import Hub
from coderstanding.offline import privacy_report
from coderstanding.server import HOST, SPEAK_PATH, VOICES_PATH, make_server
from coderstanding.voices import Voices, list_voices, prune_cache, voices_dir

RATE = 22050
FRAMES_PER_CHAR = 1000


class FakeModel:
    """Piper's `synthesize_wav`: silence, FRAMES_PER_CHAR frames per character."""

    def __init__(self, path):
        self.path = path
        self.spoken = []

    def synthesize_wav(self, text, wav_file):
        self.spoken.append(text)
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(RATE)
        wav_file.writeframes(b"\0\0" * FRAMES_PER_CHAR * len(text))


class FakeLoader:
    def __init__(self):
        self.models = []

    def __call__(self, path):
        self.models.append(FakeModel(path))
        return self.models[-1]


def add_voice(folder, name, code="en_US", quality="medium"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.onnx").write_bytes(b"model")
    config = {"audio": {"sample_rate": RATE, "quality": quality}, "language": {"code": code}}
    (folder / f"{name}.onnx.json").write_text(json.dumps(config), encoding="utf-8")


def duration(data):
    with wave.open(io.BytesIO(data)) as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "voices"
    add_voice(path, "en_US-test-medium")
    add_voice(path, "de_DE-test-low", code="de_DE", quality="low")
    return path


def test_folder_follows_the_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_HOME_ENV, str(tmp_path / "home"))
    assert voices_dir() == tmp_path / "home" / "voices"
    assert Voices().folder == tmp_path / "home" / "voices"


def test_listing_reads_name_and_language_and_skips_half_voices(folder):
    (folder / "lonely.onnx.json").write_text("{}", encoding="utf-8")   # no model
    (folder / "broken.onnx").write_bytes(b"x")
    (folder / "broken.onnx.json").write_text("not json", encoding="utf-8")
    (folder / "model-only.onnx").write_bytes(b"x")
    assert list_voices(folder) == [
        {"name": "de_DE-test-low", "lang": "de-DE", "quality": "low"},
        {"name": "en_US-test-medium", "lang": "en-US", "quality": "medium"}]
    assert list_voices(folder / "missing") == []


def test_listing_without_piper_counts_files_but_offers_none(folder):
    listing = Voices(folder, FakeLoader(), installed=lambda: False).listing()
    assert listing["voices"] == [] and listing["found"] == 2 and not listing["installed"]
    assert listing["folder"] == str(folder)
    assert "coderstanding[voices]" in listing["install"]
    assert "licence" in listing["licence"] and "Hugging Face" in listing["add"]
    assert len(Voices(folder, FakeLoader(), installed=lambda: True).listing()["voices"]) == 2


def test_speak_makes_each_clip_once_and_loads_the_model_once(folder):
    loader = FakeLoader()
    box = Voices(folder, loader, installed=lambda: True)
    clip = box.speak("en_US-test-medium", "Hello there.")
    assert duration(clip) == pytest.approx(len("Hello there.") * FRAMES_PER_CHAR / RATE)
    assert box.speak("en_US-test-medium", "Hello there.") == clip
    box.speak("en_US-test-medium", "Another chunk.")
    assert len(loader.models) == 1
    assert loader.models[0].spoken == ["Hello there.", "Another chunk."], "the cached clip is not made again"
    assert len(list((folder / "cache").glob("*.wav"))) == 2
    # a fresh server process reuses the disk cache
    again = FakeLoader()
    assert Voices(folder, again, installed=lambda: True).speak("en_US-test-medium", "Hello there.") == clip
    assert again.models == []


def test_a_replaced_voice_file_gets_new_clips(folder):
    loader = FakeLoader()
    box = Voices(folder, loader, installed=lambda: True)
    box.speak("en_US-test-medium", "Hi.")
    (folder / "en_US-test-medium.onnx").write_bytes(b"a newer model")
    box.speak("en_US-test-medium", "Hi.")
    assert len(loader.models) == 2 and len(box.models) == 1


@pytest.mark.parametrize("name", ["nope", "../voices/en_US-test-medium", str(Path("/etc/passwd")), None])
def test_speak_refuses_unknown_names_and_paths(folder, name):
    with pytest.raises(KeyError):
        Voices(folder, FakeLoader(), installed=lambda: True).speak(name, "Hi.")


def test_speak_refuses_empty_or_huge_text_and_a_missing_piper(folder):
    box = Voices(folder, FakeLoader(), installed=lambda: True)
    with pytest.raises(ValueError):
        box.speak("en_US-test-medium", "   ")
    with pytest.raises(ValueError):
        box.speak("en_US-test-medium", "x" * (voices_module.MAX_TEXT + 1))
    with pytest.raises(ValueError, match="not installed"):
        Voices(folder, FakeLoader(), installed=lambda: False).speak("en_US-test-medium", "Hi.")


def test_cache_keeps_the_newest_clips(tmp_path):
    for i in range(5):
        clip = tmp_path / f"{i}.wav"
        clip.write_bytes(b"x")
        stamp = 1_000_000_000 + i
        os.utime(clip, ns=(stamp, stamp))
    prune_cache(tmp_path, 3)
    assert sorted(path.name for path in tmp_path.glob("*.wav")) == ["2.wav", "3.wav", "4.wav"]


def test_privacy_report_names_the_folder(folder):
    line = Voices(folder, FakeLoader(), installed=lambda: False).summary()
    report = privacy_report(HOST, 8517, None, None, False, line)
    assert "built-in voices   Piper (not installed" in report
    assert f"2 voices in {folder}" in report and "Hugging Face" in report


# ---------- server routes ----------

@pytest.fixture
def served(folder):
    srv = make_server(Hub(None, fixed={}), port=0, voices=Voices(folder, FakeLoader(), installed=lambda: True))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def post(base, body, headers=None):
    request = Request(base + SPEAK_PATH, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(request) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


def test_routes_list_and_speak(served, folder):
    with urlopen(served + VOICES_PATH) as response:
        listing = json.loads(response.read())
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert [voice["name"] for voice in listing["voices"]] == ["de_DE-test-low", "en_US-test-medium"]
    status, headers, data = post(served, {"voice": "en_US-test-medium", "text": "Read me."})
    assert status == 200 and headers["Content-Type"] == "audio/wav"
    assert duration(data) > 0


def test_speak_route_refuses_other_sites_forms_and_unknown_voices(served):
    assert post(served, {"voice": "en_US-test-medium", "text": "Hi."},
                {"Origin": "https://evil.example"})[0] == 403
    request = Request(served + SPEAK_PATH, data=b"voice=x&text=y", method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(HTTPError) as refused:
        urlopen(request)
    assert refused.value.code == 415
    status, _, data = post(served, {"voice": "../x", "text": "Hi."})
    assert status == 400 and "no built-in voice" in json.loads(data)["error"]


# ---------- the real Piper (skips without it) ----------

REAL_VOICES = Path.home() / ".local" / "share" / "piper-tts" / "voices"


def test_real_piper_makes_a_wav_clip(tmp_path):
    pytest.importorskip("piper")
    models = sorted(REAL_VOICES.glob("*.onnx")) if REAL_VOICES.is_dir() else []
    model = next((path for path in models if path.with_name(path.name + ".json").is_file()), None)
    if model is None:
        pytest.skip(f"no Piper voice in {REAL_VOICES}")
    folder = tmp_path / "voices"
    folder.mkdir()
    shutil.copy(model, folder / model.name)
    shutil.copy(model.with_name(model.name + ".json"), folder / (model.name + ".json"))
    box = Voices(folder)
    short = box.speak(model.stem, "Hello.")
    long = box.speak(model.stem, "This sentence is a good deal longer than the first one was.")
    assert 0.2 < duration(short) < duration(long) < 10
    assert box.speak(model.stem, "Hello.") == short
