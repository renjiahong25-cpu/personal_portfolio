"""赛璐璐描边后处理（外部 Pillow 步骤，由系统 Python 执行）。

对 frame_dir 下每张 512x512 帧 PNG：
1. alpha 通道 MaxFilter 膨胀（默认 2px：MaxFilter(5)；N px → MaxFilter(2N+1)）
2. outline = 膨胀后 alpha - 原 alpha（恒 ≥0，天然钳制 0-255）
3. 描边色填充（默认深靛紫 #3a3356 = 58,51,86，非纯黑）：
   RGB = where(outline>0, color, 原RGB)，alpha = max(原alpha, outline)
   （用 ImageChops.difference + alpha_composite 等价实现）
原地写回。必须在 compose_sheet 之前运行，sheet png/webp 才会带描边。

用法: outline_frames.py <frame_dir> [dilate_px] [r,g,b]
"""
import os
import sys

from PIL import Image, ImageChops, ImageFilter


def outline_rgba(im, dilate_px, color):
    alpha = im.getchannel("A")
    window = 2 * dilate_px + 1  # MaxFilter(3)=1px, MaxFilter(5)=2px
    dil = alpha.filter(ImageFilter.MaxFilter(window)) if window >= 3 else alpha
    # dil 处处 >= alpha（max 滤波），difference == dil - alpha（钳制 0-255）
    band = ImageChops.difference(dil, alpha)
    ol = Image.merge("RGBA", (
        Image.new("L", im.size, color[0]),
        Image.new("L", im.size, color[1]),
        Image.new("L", im.size, color[2]),
        band,
    ))
    return Image.alpha_composite(im, ol)


def main():
    frame_dir = sys.argv[1]
    dilate_px = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    color = tuple(int(c) for c in
                  (sys.argv[3] if len(sys.argv) > 3 else "58,51,86").split(","))
    count = 0
    for fn in sorted(os.listdir(frame_dir)):
        if not fn.lower().endswith(".png"):
            continue
        p = os.path.join(frame_dir, fn)
        im = Image.open(p).convert("RGBA")
        im = outline_rgba(im, dilate_px, color)
        im.save(p)
        count += 1
    print("OUTLINE_DONE %d frames %dpx #%02x%02x%02x"
          % (count, dilate_px, color[0], color[1], color[2]))


if __name__ == "__main__":
    main()
