"""核心管线：道具 → 8 方向等距渲染 → 赛璐璐描边后处理 → spritesheet → manifest。

相机约定（真等距）：正交投影，X 轴 54.736°，Z 轴 yaw = dir * 360/dirs。
取景：ortho_scale = 2.81 * max(bbox 边长)（行业常数，保证不裁切）；
锚点：道具包围盒投影后的最低点（离相机最近的地面角点）落在画面底边正中，
与 manifest anchor=[32,64]（游戏 64px tile 的底边中心）对应。

赛璐璐风格：
- 场景光照（setup_scene）：暖白主光左上 45° 俯角 3.0 + 暖 rim 右上后方 1.5
  + 蓝调环境 #8a9ac4×0.35。材质本身是 toon emission（不受灯光影响），
  主光方向是 props.MAIN_LIGHT_DIR dot 产物的光源基准。
- 描边不靠 Blender 边缘检测：每帧渲出后走外部 Pillow（outline_frames.py），
  alpha MaxFilter 膨胀 2px，outline = 膨胀 - 原 alpha，深靛紫 #3a3356 填充，
  在 compose 之前落帧目录，sheet png/webp 均含描边。
"""
import bpy
import math
import os
import shutil
import subprocess
from mathutils import Euler, Vector

ISO_TILT = 54.736
ORTHO_FACTOR = 2.81
VIEW_DIST = 200.0
ENGINE_PREFERENCE = ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES")
PIL_PYTHON_CANDIDATES = (r"D:\miniconda3\python.exe", "python")

OUTLINE_COLOR = "58,51,86"   # 深靛紫 #3a3356（非纯黑）
OUTLINE_PX = 2               # 512px 单元格下的描边宽度（≈0.25px@64 游戏尺寸）


def _lin(c):
    """sRGB 0-1 → linear 0-1（Blender 灯光/世界颜色是 linear 空间）。"""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def setup_scene(scene):
    """赛璐璐场景级光照 + 标准色彩变换（sprite 素材用，避免 AgX 发灰）。"""
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        scene.world = world
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg is not None:
        # 世界环境：#8a9ac4 偏蓝，强度 0.35
        bg.inputs[0].default_value = (_lin(0.54), _lin(0.60), _lin(0.77), 1.0)
        bg.inputs[1].default_value = 0.35

    # 赛璐璐场景级光照（材质是 toon emission，灯光不改变最终颜色；
    # KeySun 方向 = props.MAIN_LIGHT_DIR 的 dot 产物光源基准，8 向绕视时
    # 主光始终保持在世界左前上方）：
    #   主光：暖白 (1.0,0.96,0.88)，左上 45° 俯角，强度 3.0
    #   rim ：#fff3d6 (1.0,0.95,0.84)，右上后方，强度 1.5
    for stale in ("KeySun", "FillSun", "RimSun"):
        o = bpy.data.objects.get(stale)
        if o is not None:
            bpy.data.objects.remove(o, do_unlink=True)
    _add_sun(scene, "KeySun", (math.radians(45), 0, math.radians(-45)), 3.0,
             (1.0, 0.96, 0.88))
    _add_sun(scene, "RimSun", (math.radians(45), 0, math.radians(135)), 1.5,
             (1.0, 0.95, 0.84))

    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
    except Exception as e:
        print("VIEW_SETTINGS_FALLBACK", repr(e))

    cam_data = bpy.data.cameras.new("IsoCam")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("IsoCam", cam_data)
    bpy.context.collection.objects.link(cam)
    scene.camera = cam
    return cam


def _add_sun(scene, name, rotation, energy, color=(1.0, 1.0, 1.0)):
    data = bpy.data.lights.new(name, "SUN")
    data.energy = energy
    data.color = tuple(_lin(c) for c in color)
    data.angle = math.radians(15)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.rotation_euler = rotation
    return obj


def world_bbox(scene):
    zmin = (float("inf"),) * 3
    xlo = (float("inf"), float("inf"), float("inf"))
    xhi = (float("-inf"), float("-inf"), float("-inf"))
    found = False
    for o in scene.objects:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            found = True
            xlo = tuple(min(a, b) for a, b in zip(xlo, w))
            xhi = tuple(max(a, b) for a, b in zip(xhi, w))
    if not found:
        raise RuntimeError("no mesh objects to frame")
    xlo = Vector(xlo)
    xhi = Vector(xhi)
    return xlo, xhi


def anchor_point(scene, yaw_deg):
    """实际几何（网格顶点）投影后的最低点 (x, y)。

    用顶点而非 bbox 角点：bbox 角点可能是空点（如车斗底角位于车轮之间），
    会导致道具悬空；顶点取到的才是真实"脚底"接触点。
    """
    Rt = Euler((math.radians(ISO_TILT), 0, math.radians(yaw_deg))).to_matrix().transposed()
    best_x, best_y = None, float("inf")
    for o in scene.objects:
        if o.type != "MESH":
            continue
        mw = o.matrix_world
        for v in o.data.vertices:
            q = Rt @ (mw @ v.co)
            if q.y < best_y:
                best_y = q.y
                best_x = q.x
    if best_x is None:
        raise RuntimeError("no mesh vertices to anchor")
    return best_x, best_y


def frame_iso(cam, yaw_deg, anchor_xy, ortho_scale):
    """把脚底锚点放到画面底边正中（tile anchor=[32,64] 对应位置）。"""
    ax, ay = anchor_xy
    R = Euler((math.radians(ISO_TILT), 0, math.radians(yaw_deg))).to_matrix()
    cam.rotation_euler = (math.radians(ISO_TILT), 0, math.radians(yaw_deg))
    cam.data.ortho_scale = ortho_scale
    cam.location = R @ Vector((ax, ay + ortho_scale / 2.0, VIEW_DIST))


def available_engines(scene):
    out = []
    for cand in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = cand
            out.append(cand)
        except TypeError:
            pass
    return out


def pick_engine(scene, engines=None):
    engines = engines or available_engines(scene)
    for cand in ENGINE_PREFERENCE:
        if cand in engines:
            scene.render.engine = cand
            return cand
    if engines:
        scene.render.engine = engines[0]
        return engines[0]
    raise RuntimeError("no usable render engine: %s" % engines)


def _configure_cycles(scene):
    scene.cycles.samples = 32
    scene.cycles.device = "CPU"


def _render_frames(scene, cam, name, tmp, ortho_scale, res, dirs, frames):
    for d in range(dirs):
        for f in range(frames):
            yaw = d * 360.0 / dirs
            frame_iso(cam, yaw, anchor_point(scene, yaw), ortho_scale)
            scene.render.filepath = os.path.join(tmp, "%s_%02d_%02d.png" % (name, d, f))
            bpy.ops.render.render(write_still=True)


def _find_pil_python():
    for cand in PIL_PYTHON_CANDIDATES:
        try:
            r = subprocess.run([cand, "-c", "import PIL"],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                return cand
        except Exception:
            continue
    raise RuntimeError("no python with PIL found (tried %s)" % (PIL_PYTHON_CANDIDATES,))


def _outline_frames(frame_dir):
    """赛璐璐描边后处理（外部 Pillow）：逐 512² 帧 alpha MaxFilter 膨胀 2px，
    outline = 膨胀 - 原 alpha（钳制 0-255），#3a3356 深靛紫填充，原地写回。
    在 compose 之前执行，sheet png/webp 均含描边。"""
    py = _find_pil_python()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outline_frames.py")
    r = subprocess.run([py, script, frame_dir, str(OUTLINE_PX), OUTLINE_COLOR],
                       capture_output=True, text=True, timeout=300)
    if r.stdout:
        print(r.stdout.strip())
    if r.returncode != 0:
        print("OUTLINE_STDERR", r.stderr)
        raise RuntimeError("outline postprocess failed")


def _compose_sheet(name, frame_dir, out_dir, res, dirs, frames, game_size, fps):
    py = _find_pil_python()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compose_sheet.py")
    r = subprocess.run(
        [py, script, name, frame_dir, out_dir,
         str(res), str(dirs), str(frames), str(game_size), str(fps)],
        capture_output=True, text=True, timeout=180)
    if r.stdout:
        print(r.stdout.strip())
    if r.returncode != 0:
        print("COMPOSE_STDERR", r.stderr)
        raise RuntimeError("spritesheet compose failed")


def render_prop(name, builder, out_dir, res=512, dirs=8, frames=1,
                game_size=64, fps=8):
    scene = bpy.context.scene

    builder(bpy)
    scene = bpy.context.scene  # builder 清场后重取
    bbox = world_bbox(scene)
    dims = bbox[1] - bbox[0]
    max_dim = max(dims.x, dims.y, dims.z)
    ortho_scale = ORTHO_FACTOR * max_dim

    scene.render.resolution_x = res
    scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"

    cam = setup_scene(scene)
    engines = available_engines(scene)
    engine = pick_engine(scene, engines)
    if engine.startswith("CYCLES"):
        _configure_cycles(scene)
    print("PROP", name, "engine=", engine, "ortho_scale=%.2f" % ortho_scale)

    tmp = os.path.join(out_dir, "_frames_%s" % name)
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp, exist_ok=True)

    try:
        _render_frames(scene, cam, name, tmp, ortho_scale, res, dirs, frames)
    except Exception as e:
        if not engine.startswith("CYCLES") and "CYCLES" in engines:
            print("ENGINE_FALLBACK_CYCLES", repr(e))
            scene.render.engine = "CYCLES"
            _configure_cycles(scene)
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, exist_ok=True)
            _render_frames(scene, cam, name, tmp, ortho_scale, res, dirs, frames)
        else:
            raise

    _outline_frames(tmp)
    _compose_sheet(name, tmp, out_dir, res, dirs, frames, game_size, fps)
    shutil.rmtree(tmp, ignore_errors=True)
    print("PROP_OUTPUT", os.path.join(out_dir, name + ".png"))
    print("PROP_OUTPUT", os.path.join(out_dir, name + ".webp"))
    print("PROP_OUTPUT", os.path.join(out_dir, name + ".json"))
