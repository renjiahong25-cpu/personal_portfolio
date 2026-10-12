"""管线入口。

运行:  & "D:\\blender\\blender.exe" --background --python build.py
输出:  out/<prop>.png / <prop>.webp / <prop>.json
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import bpy  # noqa: F401  (必须运行在 Blender 内)

from props import (
    build_cart, build_tent,
    build_grand_piano, build_bonfire, build_signal_tower, build_server_rack,
    build_satellite_dish, build_container_r, build_container_b, build_container_g,
    build_gantry, build_ship, build_scaffold, build_crane, build_fence,
    build_key_tile, build_tree_a, build_tree_b, build_rock, build_sign, build_dock,
)
from render_props import render_prop

OUT = os.path.join(ROOT, "out")

PROPS = [
    ("cart", build_cart),
    ("tent", build_tent),
    ("grand_piano", build_grand_piano),
    ("bonfire", build_bonfire),
    ("signal_tower", build_signal_tower),
    ("server_rack", build_server_rack),
    ("satellite_dish", build_satellite_dish),
    ("container_r", build_container_r),
    ("container_b", build_container_b),
    ("container_g", build_container_g),
    ("gantry", build_gantry),
    ("ship", build_ship),
    ("scaffold", build_scaffold),
    ("crane", build_crane),
    ("fence", build_fence),
    ("key_tile", build_key_tile),
    ("tree_a", build_tree_a),
    ("tree_b", build_tree_b),
    ("rock", build_rock),
    ("sign", build_sign),
    ("dock", build_dock),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    # 小样本/增量重渲：PF_PROP_FILTER="cart,tree_a" 环境变量过滤（不传 = 全量 21）
    props_list = PROPS
    filt = os.environ.get("PF_PROP_FILTER")
    if filt:
        keep = {s.strip() for s in filt.split(",") if s.strip()}
        props_list = [(n, b) for n, b in PROPS if n in keep]
        if not props_list:
            raise SystemExit("PF_PROP_FILTER matched no props: %r" % filt)
        print("PROP_FILTER", [n for n, _ in props_list])
    print("BUILD_START style=toon+outline#3a3356 props=%d" % len(props_list))
    failures = []
    for name, builder in props_list:
        t0 = time.time()
        try:
            # tree_b 为 0.7 倍小松树：游戏内尺寸 0.7×64=45px（manifest size 生效）
            game_size = 45 if name == "tree_b" else 64
            render_prop(name, builder, OUT, res=512, dirs=8, frames=1,
                        game_size=game_size)
            print("PROP_DONE", name, "%.1fs" % (time.time() - t0))
        except Exception as e:
            import traceback
            failures.append(name)
            print("PROP_FAIL", name, repr(e))
            traceback.print_exc()
    print("BUILD_ALL_DONE failures=%d" % len(failures), failures if failures else "")


if __name__ == "__main__":
    main()

# ---- 原待扩展名单（16 项已全部补入 props.py 并加入上方 PROPS）----
# grand_piano / bonfire / signal_tower / server_rack / satellite_dish /
# container(_r/_b/_g 3 变体) / gantry / ship / scaffold / crane / fence /
# key_tile / tree(_a 大 / _b 小 0.7× 2 变体) / rock / sign / dock
