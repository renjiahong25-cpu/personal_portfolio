"""外部 Pillow 拼 sheetsheet 步骤（Blender 内置 Python 无 PIL，由系统 Python 执行）。

用法: compose_sheet.py <name> <frame_dir> <out_dir> <res> <dirs> <frames> <game_size> <fps>

帧文件约定: frame_dir/<name>_<dir:02d>_<frame:02d>.png，
布局: dirs 沿 X（列），frames 沿 Y（行）。
"""
import json
import os
import sys

from PIL import Image


def main():
    (name, frame_dir, out_dir, res, dirs, frames, game_size, fps) = sys.argv[1:9]
    res, dirs, frames, game_size, fps = (int(v) for v in (res, dirs, frames, game_size, fps))

    w, h = res * dirs, res * frames
    sheet = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for f in range(frames):
        for d in range(dirs):
            path = os.path.join(frame_dir, "%s_%02d_%02d.png" % (name, d, f))
            im = Image.open(path).convert("RGBA")
            if im.size != (res, res):
                im = im.resize((res, res), Image.LANCZOS)
            sheet.paste(im, (d * res, f * res))

    sheet.save(os.path.join(out_dir, name + ".png"))
    try:
        sheet.save(os.path.join(out_dir, name + ".webp"), quality=90)
        print("SHEET_WEBP_OK")
    except Exception as e:
        print("SHEET_WEBP_FAIL", repr(e))

    manifest = {
        "prop": name,
        "dirs": dirs,
        "frames": frames,
        "size": [game_size, game_size],
        "anchor": [game_size // 2, game_size],
        "fps": fps,
        "sheet": [w, h],
        "res": res,
    }
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print("SHEET_DONE %dx%d" % (w, h))


if __name__ == "__main__":
    main()
