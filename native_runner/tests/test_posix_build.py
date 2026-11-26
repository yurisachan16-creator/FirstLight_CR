import pytest

from native_runner import avd_runtime, posix_build


MANIFEST = (
    '<manifest package="nullsroyale.rel.free">\n'
    '    <application android:allowBackup="true" android:label="x">\n'
    '        <meta-data android:name="io.sentry.auto-init" android:value="false"/>\n'
    "    </application>\n"
    "</manifest>\n"
)


def test_cluster_manifest_matches_powershell_build():
    text = posix_build.patch_cluster_manifest(MANIFEST, engine_count=3)
    assert (
        '<application android:name="com.supercell.clashroyale.EngineClusterApplication" '
        'android:allowNativeHeapPointerTagging="false" android:allowBackup="true"'
    ) in text
    assert text.count("<activity ") == 3
    for engine_id in range(3):
        assert f'android:name="com.supercell.clashroyale.Engine{engine_id}App"' in text
        assert f'android:process=":engine{engine_id}"' in text
    assert text.index("Engine2App") < text.index("io.sentry.auto-init")
    assert "debuggable" not in text


def test_cluster_manifest_debuggable_and_missing_anchor():
    assert 'android:debuggable="true"' in posix_build.patch_cluster_manifest(MANIFEST, 1, debuggable=True)
    with pytest.raises(RuntimeError):
        posix_build.patch_cluster_manifest("<manifest/>", 1)


def test_engine_smali_extends_game_app():
    smali = posix_build.ENGINE_SMALI.format(name="Engine0App")
    assert ".class public Lcom/supercell/clashroyale/Engine0App;" in smali
    assert ".super Lcom/supercell/clashroyale/GameApp;" in smali
    assert "{p0}" in smali


def test_avd_serial_must_be_emulator(monkeypatch):
    monkeypatch.setenv("CR_ADB_SERIAL", "emulator-5556")
    assert avd_runtime.console_port() == 5556
    monkeypatch.setenv("CR_ADB_SERIAL", "127.0.0.1:16384")
    with pytest.raises(ValueError):
        avd_runtime.serial()


def test_null_pointer_forms():
    assert all(avd_runtime._null_pointer(value) for value in ("0", "0x0", "(nil)", " 0X0 "))
    assert not avd_runtime._null_pointer(None)
    assert not avd_runtime._null_pointer("0x7f12")
