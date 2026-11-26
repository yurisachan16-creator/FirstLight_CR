import hashlib
import json
import zipfile

import pytest

from native_runner import content_profile, offline_build, offline_install


def _fake_apk(path, libg=b"libg", fingerprint=b"{}", scdb=b"scdb"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lib/arm64-v8a/libg.so", libg)
        archive.writestr("assets/fingerprint.json", fingerprint)
        archive.writestr("assets/assets.scdb", scdb)
    return path


def test_default_profile_keeps_original_pins(monkeypatch):
    monkeypatch.delenv("CR_CONTENT_PROFILE", raising=False)
    assert content_profile.name() == content_profile.UPDATE_PROFILE
    assert content_profile.default_location() == 15000199
    assert content_profile.ruleset_file_pins() == {}
    assert content_profile.runtime_fingerprint_sha256() == offline_build.supported_engine()["runtime_fingerprint_sha256"]


def test_base_profile_pins_and_location(monkeypatch):
    monkeypatch.setenv("CR_CONTENT_PROFILE", content_profile.BASE_PROFILE)
    profile = content_profile.base_profile()
    assert profile["libg_sha256"] == offline_build.supported_engine()["libg_sha256"]
    assert content_profile.default_location() == 15000002 <= profile["max_location"] < 15000199
    pins = content_profile.ruleset_file_pins()
    assert set(pins) == {"base-15.535.3/fingerprint.json", "base-15.535.3/assets.scdb"}
    assert pins["base-15.535.3/assets.scdb"] == profile["assets_sha256"]


def test_unknown_profile_is_rejected(monkeypatch):
    monkeypatch.setenv("CR_CONTENT_PROFILE", "base-16")
    with pytest.raises(ValueError):
        content_profile.name()


def test_original_apk_accepts_matching_libg_payload(tmp_path, monkeypatch):
    apk = _fake_apk(tmp_path / "watermarked.apk", libg=b"engine")
    release = dict(offline_build.supported_engine(), libg_sha256=hashlib.sha256(b"engine").hexdigest())
    monkeypatch.setattr(offline_build, "supported_engine", lambda: release)
    assert offline_build.original_apk_problem(apk) is None
    other = _fake_apk(tmp_path / "v16.apk", libg=b"other engine")
    assert "differs from the supported engine" in offline_build.original_apk_problem(other)
    not_zip = tmp_path / "broken.apk"
    not_zip.write_bytes(b"not a zip")
    assert "not a readable" in offline_build.original_apk_problem(not_zip)


def test_base_profile_resource_validation(monkeypatch):
    monkeypatch.setenv("CR_CONTENT_PROFILE", content_profile.BASE_PROFILE)
    pins = content_profile.pinned_runtime_files()
    offline_install.validate_resources(dict(pins))
    with pytest.raises(ValueError, match="seed-base-content"):
        offline_install.validate_resources({**pins, "assets.scdb": "0" * 64})


def test_seed_payloads_verify_apk_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("CR_CONTENT_PROFILE", content_profile.BASE_PROFILE)
    fingerprint = json.dumps({"version": "15.535.3"}).encode()
    apk = _fake_apk(tmp_path / "base.apk", fingerprint=fingerprint, scdb=b"bundled")
    profile = dict(
        content_profile.base_profile(),
        fingerprint_sha256=hashlib.sha256(fingerprint).hexdigest(),
        assets_sha256=hashlib.sha256(b"bundled").hexdigest(),
    )
    monkeypatch.setattr(content_profile, "base_profile", lambda: profile)
    assert content_profile.seed_payloads(apk) == {"fingerprint.json": fingerprint, "assets.scdb": b"bundled"}
    tampered = _fake_apk(tmp_path / "tampered.apk", fingerprint=fingerprint, scdb=b"other")
    with pytest.raises(ValueError, match="assets.scdb"):
        content_profile.seed_payloads(tampered)
