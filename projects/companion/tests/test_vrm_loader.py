"""A-level checks for VRM container inspection, the model registry and licence records.

No real model is required except in the last test, which skips when the (git-ignored)
sample file is absent. Corrupted / truncated / non-VRM files must produce a clear
VrmFormatError - that is the V12 fallback trigger, so it is asserted here, not only
on a desktop.
"""
import json
import struct
from pathlib import Path

import pytest

from app.pet3d import vrm_loader


def glb_bytes(json_payload: dict) -> bytes:
    raw = json.dumps(json_payload).encode("utf-8")
    padding = (-len(raw)) % 4
    raw += b" " * padding
    chunk = struct.pack("<I", len(raw)) + struct.pack("<I", 0x4E4F534A) + raw
    total = 12 + len(chunk)
    return struct.pack("<III", 0x46546C67, 2, total) + chunk


def glb_with_spec(spec, tmp_path, name="m.vrm", extra_meta=None):
    vrm = {"specVersion": spec, "meta": dict({"title": "t"}, **(extra_meta or {}))}
    payload = {"asset": {"version": "2.0"}, "extensions": {"VRMC_vrm": vrm}}
    path = tmp_path / name
    path.write_bytes(glb_bytes(payload))
    return path


def test_vrm_1_and_0x_are_distinguished(tmp_path):
    assert vrm_loader.detect_version(glb_with_spec("1.0", tmp_path)) == vrm_loader.VRM_1_0
    assert vrm_loader.detect_version(glb_with_spec("0.x", tmp_path)) == vrm_loader.VRM_0_X


def test_plain_gltf_without_vrm_extension_is_unknown(tmp_path):
    path = tmp_path / "plain.vrm"
    path.write_bytes(glb_bytes({"asset": {"version": "2.0"}}))
    assert vrm_loader.detect_version(path) == vrm_loader.UNKNOWN


def test_truncated_non_glb_and_empty_files_raise_format_error(tmp_path):
    good = glb_with_spec("1.0", tmp_path, "good.vrm")
    data = good.read_bytes()

    truncated = tmp_path / "trunc.vrm"
    truncated.write_bytes(data[: len(data) // 2])
    with pytest.raises(vrm_loader.VrmFormatError):
        vrm_loader.detect_version(truncated)

    not_glb = tmp_path / "junk.vrm"
    not_glb.write_bytes(b"RIFF____WEBPVP8 ....")
    with pytest.raises(vrm_loader.VrmFormatError):
        vrm_loader.detect_version(not_glb)

    tiny = tmp_path / "tiny.vrm"
    tiny.write_bytes(b"glTF")
    with pytest.raises(vrm_loader.VrmFormatError):
        vrm_loader.detect_version(tiny)

    empty = tmp_path / "empty.vrm"
    empty.write_bytes(b"")
    with pytest.raises(vrm_loader.VrmFormatError):
        vrm_loader.inspect(empty)


def test_inspect_reads_meta_and_digest(tmp_path):
    path = glb_with_spec(
        "1.0",
        tmp_path,
        "meta.vrm",
        extra_meta={"authors": ["Some Author"], "licenseUrl": "https://example.invalid/lic"},
    )
    info = vrm_loader.inspect(path, verify_digest=True)
    assert info.is_vrm1 and info.title == "t" and info.author == "Some Author"
    assert info.license_url == "https://example.invalid/lic"
    assert len(info.sha256) == 64 and info.byte_size == path.stat().st_size
    assert "VRM 1.0" in info.summary()


def test_resolve_reports_missing_model_with_directory_listing(tmp_path):
    glb_with_spec("1.0", tmp_path, "someone_elses_model.vrm")
    with pytest.raises(vrm_loader.VrmFormatError) as err:
        vrm_loader.resolve(tmp_path, "not_here.vrm")
    assert "someone_elses_model.vrm" in str(err.value)


def test_resolve_rejects_registered_model_with_wrong_size(tmp_path):
    entry = vrm_loader.DEFAULT_MODEL
    fake = tmp_path / entry
    fake.write_bytes(b"short but registered filename")
    with pytest.raises(vrm_loader.VrmFormatError) as err:
        vrm_loader.resolve(tmp_path, None)
    assert "大小" in str(err.value)


def test_every_registered_entry_documents_licence_and_source():
    for name, entry in vrm_loader.REGISTRY.items():
        assert entry.author and entry.license_name and entry.license_url and entry.source_url
        assert len(entry.sha256) == 64, f"{name}: licence entry must carry a real digest"
        assert entry.byte_size > 0
    notices = "\n".join(vrm_loader.notices())
    assert "VRM Public License" in notices and "pixiv" in notices


def test_only_licensing_cleared_models_may_be_registered():
    # docs/03 card 1 red line: the registry is the licence gate. Anything outside the
    # cleared set must never appear here (Seed-san et al. stay out until verified).
    allowed = {"VRM1_Constraint_Twist_Sample.vrm"}
    assert set(vrm_loader.REGISTRY) == allowed


SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "models" / vrm_loader.DEFAULT_MODEL


@pytest.mark.skipif(not SAMPLE.exists(), reason="licensed sample model is git-ignored")
def test_shipped_sample_matches_its_registry_record():
    info = vrm_loader.inspect(SAMPLE, verify_digest=True)
    entry = vrm_loader.REGISTRY[vrm_loader.DEFAULT_MODEL]
    assert info.is_vrm1
    assert info.sha256 == entry.sha256
    assert info.byte_size == entry.byte_size
    assert "pixiv" in info.author
