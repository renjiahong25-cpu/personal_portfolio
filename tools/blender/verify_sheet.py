"""程序化验证 spritesheet：8 方向非空 / 底边锚定 / 透明背景 / 黑边检查。

判据（与 README 验收口径一致）：
- 每个方向 tile 非空
- bottom_gap <= 6：脚底（投影最低顶点）贴住 tile 底边
- 四角 alpha < 8：透明背景
- 半透明边缘平均亮度 > 40：无黑色 fringing

cx 仅打印不做阈值判断：锚点是"离相机最近的地面角点"而非 bbox 中心，
非对称道具（cart/crane/ship 等）投影后 bbox 中心天然偏离 256，
cart/tent 验收记录（dir00 cx=322.5 ↔ dir04 cx=188.5 镜像）即此行为。
"""
import sys
from PIL import Image

name = sys.argv[1] if len(sys.argv) > 1 else "cart"
res = 512
im = Image.open("out/%s.png" % name).convert("RGBA")
W, H = im.size
dirs = W // res
px = im.load()

print("== %s.png  %dx%d  dirs=%d ==" % (name, W, H, dirs))
all_ok = True
for d in range(dirs):
    x0 = d * res
    minx, miny, maxx, maxy = res, res, -1, -1
    opaque = 0
    for y in range(res):
        for x in range(res):
            r, g, b, a = px[x0 + x, y]
            if a > 16:
                opaque += 1
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    if maxx < 0:
        print("dir%02d  EMPTY" % d)
        all_ok = False
        continue
    w = maxx - minx + 1
    h = maxy - miny + 1
    cx = (minx + maxx) / 2.0
    bottom_gap = res - 1 - maxy
    # 锚定：底边应贴近 tile 底（gap 小）
    anchor_ok = bottom_gap <= 6
    # 透明背景：tile 四角 alpha 应近 0
    corners = [px[x0, 0], px[x0 + res - 1, 0],
               px[x0, res - 1], px[x0 + res - 1, res - 1]]
    trans_ok = all(c[3] < 8 for c in corners)
    # 黑边检查：半透明边缘像素（16<a<=250）平均色，若接近黑则 fringing
    fr = 0
    fr_n = 0
    for y in range(miny, maxy + 1):
        for x in range(minx, maxx + 1):
            r, g, b, a = px[x0 + x, y]
            if 16 < a <= 250:
                fr += r + g + b
                fr_n += 1
    fr_avg = (fr / 3.0 / fr_n) if fr_n else 255.0
    edge_ok = fr_avg > 40  # 边缘平均亮度远低于 40 视为黑边
    ok = anchor_ok and trans_ok and edge_ok
    if not ok:
        all_ok = False
    print("dir%02d  bbox=(%d,%d)-(%d,%d) %dx%d  cx=%.1f  bottom_gap=%d  opaque=%d  edge_luma=%.1f  trans=%s  %s"
          % (d, minx, miny, maxx, maxy, w, h, cx, bottom_gap, opaque, fr_avg,
             "OK" if trans_ok else "FAIL", "OK" if ok else "CHECK"))

print("RESULT", "PASS" if all_ok else "NEEDS_REVIEW")
