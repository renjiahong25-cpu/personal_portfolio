"""VRM file inspection, model registry and licence bookkeeping.

A `.vrm` is a glTF binary: a 12 byte header then a JSON chunk. `VRMC_vrm` in that JSON
carries `specVersion`, which is how we tell VRM 1.0 from VRM 0.x before handing the file
to @pixiv/three-vrm (which needs the version to pick the right loader path).

Only models with a verified licence may be registered here (docs/03 section 3.4, card 1
red line). Anything else must be imported by the user, who owns that decision.
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

GLB_MAGIC = 0x46546C67  # b'glTF'

VRM_1_0 = "1.0"
VRM_0_X = "0.x"
UNKNOWN = "unknown"

DEFAULT_DIRNAME = "models"


class VrmFormatError(ValueError):
    """The file is not a usable VRM (bad container, truncated, or unparsable metadata)."""


@dataclass(frozen=True)
class VrmInfo:
    path: Path
    version: str
    title: str = ""
    author: str = ""
    license_url: str = ""
    byte_size: int = 0
    sha256: str = ""

    @property
    def is_vrm1(self) -> bool:
        return self.version == VRM_1_0

    def summary(self) -> str:
        label = {"1.0": "VRM 1.0", "0.x": "VRM 0.x"}.get(self.version, "未知版本")
        who = f" · {self.author}" if self.author else ""
        return f"{self.name}（{label}{who}）"

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class LicensedModel:
    """A model plus the licence facts that must be reproduced wherever it ships."""

    filename: str
    title: str
    author: str
    license_name: str
    license_url: str
    source_url: str
    sha256: str
    byte_size: int
    notes: str = ""


# The single model docs/03 section 3.4 cleared for use: shipped inside the pixiv/three-vrm
# example set, embedded metadata states VRM Public License 1.0, (c) 2022 pixiv Inc.
REGISTRY: dict[str, LicensedModel] = {
    "VRM1_Constraint_Twist_Sample.vrm": LicensedModel(
        filename="VRM1_Constraint_Twist_Sample.vrm",
        title="VRM1_Constraint_Twist_Sample",
        author="(c) 2022 pixiv Inc.",
        license_name="VRM Public License 1.0",
        license_url="https://vrm.dev/licenses/1.0/",
        source_url=(
            "https://raw.githubusercontent.com/pixiv/three-vrm/dev/"
            "packages/three-vrm/examples/models/VRM1_Constraint_Twist_Sample.vrm"
        ),
        # SHA256 measured from assets/models on 2026-10-15 and re-verified against a
        # fresh re-download from source_url the same day (both digests identical).
        sha256="12c2b97e95e700783a6a550dc0eee2d7880aeedccef9ae67bc4c5a2f0f2631a2",
        byte_size=10776032,
        notes="VRM 1.0, exercises SpringBone + Constraints + expressions.",
    )
}

DEFAULT_MODEL = "VRM1_Constraint_Twist_Sample.vrm"


def models_dir(root: Path | str | None = None) -> Path:
    base = Path(root) if root else Path(__file__).resolve().parent.parent.parent
    return base / "assets" / DEFAULT_DIRNAME


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_glb_json(path: Path) -> dict:
    """Parse the JSON chunk of a glTF/GLB container."""
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12:
            raise VrmFormatError(f"{path.name}: 文件不足 12 字节，不是 GLB")
        magic, version, _length = struct.unpack("<III", header)
        if magic != GLB_MAGIC:
            raise VrmFormatError(f"{path.name}: 不是 glTF/VRM 二进制容器")
        if version != 2:
            raise VrmFormatError(f"{path.name}: GLB 版本 {version}，只支持 2")
        chunk_len, chunk_type = struct.unpack("<II", handle.read(8))
        if chunk_type != 0x4E4F534A:  # b'JSON'
            raise VrmFormatError(f"{path.name}: 第一个数据块不是 JSON")
        raw = handle.read(chunk_len)
        if len(raw) < chunk_len:
            raise VrmFormatError(f"{path.name}: JSON 块被截断")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VrmFormatError(f"{path.name}: JSON 解析失败 {exc}") from exc


def detect_version(path: Path | str) -> str:
    """VRM 1.0 / 0.x / unknown, from `VRMC_vrm.specVersion` in the GLB metadata."""
    info = read_glb_json(Path(path))
    meta = info.get("extensions", {}).get("VRMC_vrm")
    if not isinstance(meta, dict):
        return UNKNOWN
    spec = str(meta.get("specVersion", "")).strip()
    if spec.startswith("1."):
        return VRM_1_0
    if spec.startswith("0."):
        return VRM_0_X
    return UNKNOWN


def inspect(path: Path | str, verify_digest: bool = False) -> VrmInfo:
    path = Path(path)
    if not path.exists():
        raise VrmFormatError(f"模型文件不存在：{path}")
    size = path.stat().st_size
    if size == 0:
        raise VrmFormatError(f"{path.name}: 文件为空")
    glb = read_glb_json(path)
    meta = glb.get("extensions", {}).get("VRMC_vrm")
    meta = meta if isinstance(meta, dict) else {}
    spec = str(meta.get("specVersion", "")).strip()
    version = VRM_1_0 if spec.startswith("1.") else VRM_0_X if spec.startswith("0.") else UNKNOWN
    digest = sha256_of(path) if verify_digest else ""
    return VrmInfo(
        path=path,
        version=version,
        title=str(meta.get("meta", {}).get("title", "")) if isinstance(meta.get("meta"), dict) else "",
        author=str(meta.get("meta", {}).get("authors", [""])[0] if isinstance(meta.get("meta"), dict) and meta["meta"].get("authors") else ""),
        license_url=str(meta.get("meta", {}).get("licenseUrl", "")) if isinstance(meta.get("meta"), dict) else "",
        byte_size=size,
        sha256=digest,
    )


def licensed_models(directory: Path | str | None = None) -> list[LicensedModel]:
    """Registered models that are actually present on disk."""
    directory = Path(directory) if directory else models_dir()
    return [m for m in REGISTRY.values() if (directory / m.filename).exists()]


def resolve(directory: Path | str | None = None, name: str | None = None) -> Path:
    """Path of the model to render, or a clear error.

    Raises VrmFormatError rather than silently falling back, so the caller can decide
    between the 2D pet and a visible error message.
    """
    directory = Path(directory) if directory else models_dir()
    filename = name or DEFAULT_MODEL
    path = directory / filename
    if not path.exists():
        available = ", ".join(sorted(p.name for p in directory.glob("*.vrm"))) or "（目录为空）"
        raise VrmFormatError(
            f"找不到模型 {filename}。{directory} 下现有：{available}。"
            f"请按 README 的“如何加新模型”放入文件后重启。"
        )
    if filename in REGISTRY:
        entry = REGISTRY[filename]
        if entry.byte_size and path.stat().st_size != entry.byte_size:
            raise VrmFormatError(
                f"{filename} 大小 {path.stat().st_size} 与登记的 {entry.byte_size} 不符，文件可能损坏或被替换。"
            )
    return path


def notices() -> list[str]:
    """Lines for THIRD_PARTY_NOTICES.md."""
    lines = []
    for model in REGISTRY.values():
        lines.append(
            f"- `{model.filename}` - {model.title}，作者 {model.author}。"
            f"许可：{model.license_name}（{model.license_url}）。"
            f"来源：{model.source_url}。SHA256 `{model.sha256}`，{model.byte_size} 字节。{model.notes}"
        )
    return lines