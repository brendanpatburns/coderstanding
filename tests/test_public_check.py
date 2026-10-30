"""The public export's checker (scripts/public_check.py): excludes, private markers, hits."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "public_check", Path(__file__).resolve().parents[1] / "scripts" / "public_check.py")
pc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc)


def test_markers_drop_private_blocks_and_unwrap_public_lines():
    text = ("a\n<!-- private:start -->\nsecret\n<!-- private:end -->\n"
            "  <!-- public-line: - public -->\n# private:start\nx\n# private:end\nb\n")
    assert pc.apply_markers(text) == "a\n  - public\nb\n"
    assert pc.apply_markers("mentions `private:start` inline\n") == "mentions `private:start` inline\n"


@pytest.mark.parametrize("text", ["# private:start\nx\n", "# private:end\n",
                                  "# private:start\n# private:start\n"])
def test_unbalanced_markers_raise(text):
    with pytest.raises(ValueError):
        pc.apply_markers(text)


def test_prepare_excludes_paths_and_empty_folders(tmp_path):
    (tmp_path / "docs" / "private").mkdir(parents=True)
    (tmp_path / "docs" / "private" / "LAB.md").write_text("lab\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy-x.sh").write_text("x\n")
    (tmp_path / "keep.md").write_text("# private:start\nhidden\n# private:end\nshown\n")
    (tmp_path / pc.EXCLUDE_FILE).write_text("# note\ndocs/private/\nscripts/deploy-*.sh\n")
    removed = pc.prepare(tmp_path)
    assert sorted(removed) == ["docs/private/LAB.md", "scripts/deploy-x.sh"]
    assert not (tmp_path / "docs").exists() and not (tmp_path / "scripts").exists()
    assert (tmp_path / "keep.md").read_text() == "shown\n"


def hits(tmp_path, files, deny="secretname\nre:\\bmyhost\\b\n"):
    for name, body in files.items():
        path = tmp_path / "tree" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body) if isinstance(body, bytes) else path.write_text(body)
    (tmp_path / "deny.txt").write_text(deny)
    rules = pc.generic_rules() + pc.load_denylist(tmp_path / "deny.txt")
    return {(rel, kind.split(" (")[0]) for rel, _, kind, _ in pc.find_hits(tmp_path / "tree", rules)}


def test_generic_patterns_and_denylist(tmp_path):
    found = hits(tmp_path, {
        "a.txt": "mail me at " + "jo" + "@mail.example.de",
        "b.txt": "server 192." + "168.1.5",
        "c.txt": "/ho" + "me/alice/code",
        "d.txt": "-----BEGIN RSA " + "PRIVATE KEY-----",
        "e.txt": "token = 'gh" + "p_" + "a" * 36 + "'",
        "f.txt": "SecretName lives on MyHost",
        "secretname.md": "x",
        "g.bin": b"\0\1\2",
        ".env": "A=1",
    })
    assert ("a.txt", "email") in found and ("b.txt", "private IP") in found
    assert ("c.txt", "home path") in found and ("d.txt", "private key") in found
    assert ("e.txt", "token") in found and ("f.txt", "denylist") in found
    assert ("secretname.md", "denylist") in found and ("g.bin", "binary file") in found
    assert (".env", "secret-looking file") in found


def test_clean_text_has_no_hits(tmp_path):
    assert hits(tmp_path, {
        "ok.txt": "bind 127.0.0.1 or 0.0.0.0; folder /home/me/code; "
                  "1+someone@users.noreply.github.com; a@example.com; version 1.2.3.4; myhosting",
    }) == set()
