"""程序化道具建模（bpy 基本体，可复现）。

约定：
- 每个 build 函数先清场再建对象
- 原点 = 地面中心（世界 (0,0,0)），物体整体归一到 TARGET_HEIGHT ≈ 60 单位高
- 根对象（Empty）名 = 道具名，所有部件为其子级
- 道具"正面"朝向 -Y（方向 0 的相机从 -Y 侧观察）
- 材质统一赛璐璐（toon_material）：Geometry→Dot(主光方向)→ColorRamp(CONSTANT 硬停)
  →Emission，完全不透明、无 Principled 柔渐变；颜色严格按"吉卜力天空之城"色板
  （hex 为 sRGB，进节点前转 linear，Standard 变换下渲染结果与色板 hex 一致）
"""
import bpy
import bmesh
import math
import random
from mathutils import Matrix, Vector

TARGET_HEIGHT = 60.0


def clear_scene(bpy):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.curves, bpy.data.images):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def make_mat(bpy, name, rgb, roughness=0.8):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    # 节点名/socket 名会随 Blender 界面语言本地化，必须按类型/多候选匹配
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        sock = _sock(bsdf, "Base Color", "BaseColor")
        if sock is not None:
            sock.default_value = (*rgb, 1.0)
        sock = _sock(bsdf, "Roughness")
        if sock is not None:
            sock.default_value = roughness
        sock = _sock(bsdf, "Metallic")
        if sock is not None:
            sock.default_value = 0.0
    return mat


def _sock(bsdf, *cands):
    for c in cands:
        for sock in bsdf.inputs:
            if c in (sock.name, sock.identifier):
                return sock
    return None


# ---------------------------------------------------------------- 赛璐璐（cel）材质

def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def srgb_to_linear(c):
    """sRGB 0-1 → linear 0-1（Blender 节点颜色是 linear，不转会偏暗偏饱和）。"""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_lin(h):
    return tuple(srgb_to_linear(c) for c in hex_rgb(h))


def scale_hex(h, f):
    """sRGB hex 按通道等比缩放（f<1 压暗 / f>1 提亮，钳制 0-1），返回新 hex。"""
    r, g, b = hex_rgb(h)
    return "#%02x%02x%02x" % (int(min(max(r * f, 0.0), 1.0) * 255 + 0.5),
                              int(min(max(g * f, 0.0), 1.0) * 255 + 0.5),
                              int(min(max(b * f, 0.0), 1.0) * 255 + 0.5))


# 主光方向（世界坐标，场景指向光的方向）：左前上方、俯角 45°。
# 与 render_props.setup_scene 的 KeySun（rotation (45°,0,-45°) → 光线朝 (+.5,+.5,-.707)）对应：
# 方向 0 相机（-Y 侧观察，画面右=+X）下光即从画面左上方来。
MAIN_LIGHT_DIR = (-0.5, -0.5, 0.7071)


def toon_material(bpy, name, base_hex, dark_hex, bright_hex=None, extra_stops=None):
    """赛璐璐材质：Geometry.Normal · Dot(主光方向) → ColorRamp(CONSTANT 硬停) → Emission → Output。

    三段硬停：dot < 0.30 → 暗部 dark_hex；0.30 ≤ dot < 0.72 → 中部 base_hex；
    dot ≥ 0.72 → 亮部 bright_hex（默认 base_hex 提亮 10%）。
    EEVEE 下最稳的硬边卡通光：不依赖场景灯光、无 Principled 柔渐变，完全不透明。
    extra_stops: [(pos, hex), ...] 追加硬停段，按 pos 排序并入。
    """
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (420, 0)
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.location = (200, 0)
    emit.inputs[1].default_value = 1.0  # linear 颜色 ≤1，Standard 变换不 clipping
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (-20, 0)
    ramp.color_ramp.interpolation = "CONSTANT"

    if bright_hex is None:
        bright_hex = scale_hex(base_hex, 1.1)
    stops = sorted([(0.0, dark_hex), (0.30, base_hex), (0.72, bright_hex)] +
                   list(extra_stops or []), key=lambda s: s[0])
    els = ramp.color_ramp.elements
    while len(els) > 1:
        ramp.color_ramp.elements.remove(els[-1])
    els[0].position = stops[0][0]
    els[0].color = (*hex_lin(stops[0][1]), 1.0)
    for pos, hx in stops[1:]:
        e = ramp.color_ramp.elements.new(pos)
        e.color = (*hex_lin(hx), 1.0)

    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = MAIN_LIGHT_DIR
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    normal = None
    for s in geo.outputs:
        if s.identifier == "Normal" or s.name in ("Normal", "法线"):
            normal = s
            break
    if normal is None:
        normal = geo.outputs[1]
    nt.links.new(normal, dot.inputs[0])
    nt.links.new(dot.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], emit.inputs[0])
    nt.links.new(emit.outputs[0], out.inputs[0])
    return mat


def _link(bpy, obj):
    bpy.context.collection.objects.link(obj)
    return obj


def _world_zrange(bpy):
    zmin, zmax = float("inf"), float("-inf")
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            z = (o.matrix_world @ Vector(c)).z
            zmin, zmax = min(zmin, z), max(zmax, z)
    if zmin == float("inf"):
        raise RuntimeError("no mesh objects in scene")
    return zmin, zmax


def _world_bbox(bpy):
    xlo = Vector((1e9, 1e9, 1e9))
    xhi = Vector((-1e9, -1e9, -1e9))
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        mw = o.matrix_world
        for c in o.bound_box:
            w = mw @ Vector(c)
            xlo = Vector(tuple(min(a, b) for a, b in zip(xlo, w)))
            xhi = Vector(tuple(max(a, b) for a, b in zip(xhi, w)))
    return xlo, xhi


def _finish(bpy, name, target=TARGET_HEIGHT):
    """命名根 Empty、父化部件、归一化到 target（地面保持在 z=0）。

    归一化对象 = bbox 最大边（而非单纯高度）：高主导道具即"高 60"；
    扁宽道具（key_tile/fence/dock/ship 等）按宽度归一，保证 ortho_scale
    ≈ 2.81×60 恒定、道具在 64px tile 内占地合理（否则扁道具会缩成一条）。
    高度主导道具的产物与此前的纯高度归一完全一致（cart/tent 不受影响）。
    """
    root = bpy.data.objects.new(name, None)
    _link(bpy, root)
    parts = [o for o in bpy.data.objects if o.type == "MESH"]
    for p in parts:
        p.parent = root
    bpy.context.view_layer.update()

    zmin, _ = _world_zrange(bpy)
    if zmin < 0:
        shift = Matrix.Translation(Vector((0, 0, -zmin)))
        for p in parts:
            p.matrix_world = shift @ p.matrix_world
        bpy.context.view_layer.update()

    xlo, xhi = _world_bbox(bpy)
    dims = xhi - xlo
    f = target / max(dims.x, dims.y, dims.z)
    if abs(f - 1.0) > 1e-6:
        for p in parts:
            p.matrix_world = Matrix.Scale(f, 4) @ p.matrix_world
    bpy.context.view_layer.update()


# ---------------------------------------------------------------- 基本体助手


def _box(bpy, name, size, loc, mat=None, rot=None):
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    o.location = loc
    if rot is not None:
        o.rotation_euler = rot
    if mat is not None:
        o.data.materials.append(mat)
    return o


def _cyl(bpy, name, radius, depth, loc, mat=None, rot=None, vertices=24):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth)
    o = bpy.context.active_object
    o.name = name
    o.location = loc
    if rot is not None:
        o.rotation_euler = rot
    if mat is not None:
        o.data.materials.append(mat)
    return o


def _cone(bpy, name, r, h, loc, mat=None, vertices=12):
    bpy.ops.mesh.primitive_cone_add(vertices=vertices, radius1=r, radius2=0, depth=h)
    o = bpy.context.active_object
    o.name = name
    o.location = loc
    if mat is not None:
        o.data.materials.append(mat)
    return o


def _sphere(bpy, name, r, loc, mat=None, segments=16, rings=12):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, radius=r)
    o = bpy.context.active_object
    o.name = name
    o.location = loc
    if mat is not None:
        o.data.materials.append(mat)
    return o


def _torus(bpy, name, major, minor, loc, mat=None, rot=None):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor)
    o = bpy.context.active_object
    o.name = name
    o.location = loc
    if rot is not None:
        o.rotation_euler = rot
    if mat is not None:
        o.data.materials.append(mat)
    return o


def _plane(bpy, name, size, loc, mat=None, rot=None):
    bpy.ops.mesh.primitive_plane_add(size=1)
    o = bpy.context.active_object
    o.name = name
    o.scale = (size[0], size[1], 1.0)
    o.location = loc
    if rot is not None:
        o.rotation_euler = rot
    if mat is not None:
        o.data.materials.append(mat)
    return o


def _rod(bpy, name, p0, p1, radius, mat=None, vertices=12):
    """两定点之间的圆柱杆（如吊索/天线/馈源臂）。"""
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=d.length)
    o = bpy.context.active_object
    o.name = name
    o.location = (p0 + p1) * 0.5
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = d.to_track_quat("Z", "Y")
    if mat is not None:
        o.data.materials.append(mat)
    return o


def _prism(bpy, name, outline, z0, z1, mat=None):
    """XY 轮廓（顺时针或逆时针均可）沿 Z 拉成的棱柱（船体/琴身等）。"""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bot = [bm.verts.new((x, y, z0)) for x, y in outline]
    top = [bm.verts.new((x, y, z1)) for x, y in outline]
    bm.faces.new(list(reversed(bot)))
    bm.faces.new(top)
    n = len(outline)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bot[i], bot[j], top[j], top[i]))
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    _link(bpy, o)
    if mat is not None:
        o.data.materials.append(mat)
    return o


def make_emit(bpy, name, rgb, strength=1.5, base=None, roughness=0.5):
    """自发光材质（Principled+Emission，保留兼容；新道具一律用 toon_material）。"""
    mat = make_mat(bpy, name, base or rgb, roughness=roughness)
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        s = _sock(bsdf, "Emission Color", "EmissionColor", "Emission")
        if s is not None:
            s.default_value = (*rgb, 1.0)
        s = _sock(bsdf, "Emission Strength", "EmissionStrength")
        if s is not None:
            s.default_value = strength
    return mat


def set_alpha(mat, alpha):
    """半透明（EEVEE Next 5.2：BSDF Alpha + surface_render_method=BLENDED）。
    赛璐璐风格禁用半透明，保留仅为 API 兼容。"""
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        s = _sock(bsdf, "Alpha")
        if s is not None:
            s.default_value = alpha
    for prop, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(mat, prop, val)
        except Exception:
            pass
    return mat


def build_cart(bpy):
    """小推车：立方体车斗 + 4 圆柱车轮 + 斜面篷布，正面朝 -Y。"""
    clear_scene(bpy)
    wood = toon_material(bpy, "cart_wood", "#c8945e", "#8a5f34")
    wheel_m = toon_material(bpy, "cart_wheel", "#8a94a4", scale_hex("#8a94a4", 0.65))
    canvas = toon_material(bpy, "cart_canvas", "#c8945e", "#8a5f34")

    bpy.ops.mesh.primitive_cube_add(size=1)
    bed = bpy.context.active_object
    bed.name = "cart_bed"
    bed.scale = (44, 36, 18)
    bed.location = (0, 0, 26)
    bed.data.materials.append(wood)

    for i, (x, y) in enumerate(((-17, -10), (17, -10), (-17, 10), (17, 10))):
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=8, depth=4)
        w = bpy.context.active_object
        w.name = "cart_wheel_%d" % i
        w.rotation_euler = (0, math.radians(90), 0)
        w.location = (x, y, 8)
        w.data.materials.append(wheel_m)

    bpy.ops.mesh.primitive_cube_add(size=1)
    cp = bpy.context.active_object
    cp.name = "cart_canopy"
    cp.scale = (40, 30, 2)
    cp.location = (0, 2, 46)
    cp.rotation_euler = (math.radians(18), 0, 0)
    cp.data.materials.append(canvas)

    _finish(bpy, "cart")


def build_tent(bpy):
    """帐篷：四棱锥帐篷体 + 前坡门帘平面，门朝 -Y。"""
    clear_scene(bpy)
    fabric = toon_material(bpy, "tent_fabric", "#ff8a4a", "#c45a2a")
    door_m = toon_material(bpy, "tent_door", "#fdf0d8", scale_hex("#fdf0d8", 0.65))

    me = bpy.data.meshes.new("tent_body")
    bm = bmesh.new()
    bl = bm.verts.new((-16, -20, 0))
    br = bm.verts.new((16, -20, 0))
    fr = bm.verts.new((16, 20, 0))
    fl = bm.verts.new((-16, 20, 0))
    ap = bm.verts.new((0, 0, 52))
    bm.faces.new((bl, br, ap))
    bm.faces.new((br, fr, ap))
    bm.faces.new((fr, fl, ap))
    bm.faces.new((fl, bl, ap))
    bm.faces.new((bl, fl, fr, br))
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    body = bpy.data.objects.new("tent_body", me)
    _link(bpy, body)
    body.data.materials.append(fabric)

    slope = math.atan2(20, 52)
    theta = math.pi / 2 - slope
    n = Vector((0, -math.cos(slope), math.sin(slope)))
    bpy.ops.mesh.primitive_plane_add(size=1)
    door = bpy.context.active_object
    door.name = "tent_door"
    door.scale = (10, 14, 1)
    door.rotation_euler = (theta, 0, 0)
    cy = -20 + 20 * (7.0 / 52.0)
    door.location = Vector((0, cy, 7)) + n * 0.15
    door.data.materials.append(door_m)

    _finish(bpy, "tent")


# ---------------------------------------------------------------- 16 个扩展道具


def build_grand_piano(bpy):
    """三角钢琴：梯形琴身 + 掀开顶盖 + 白键 10/黑键 7 + 3 琴腿，键盘朝 -Y。"""
    clear_scene(bpy)
    body_m = toon_material(bpy, "gp_body", "#1a1a1f", "#0c0c10")
    lid_m = toon_material(bpy, "gp_lid", "#1a1a1f", "#0c0c10")
    white_m = toon_material(bpy, "gp_white", "#f5f2ea", scale_hex("#f5f2ea", 0.65))
    black_m = toon_material(bpy, "gp_black", "#0c0c10", scale_hex("#0c0c10", 0.5))
    leg_m = toon_material(bpy, "gp_leg", "#1a1a1f", "#0c0c10")

    # 琴身：键盘侧（前，-Y）宽，左侧（翼侧）斜收，背面短边
    outline = [(-24, -20), (24, -20), (24, 22), (14, 27), (2, 27)]
    _prism(bpy, "gp_body", outline, 6, 24, body_m)

    # 顶盖：薄板沿左斜边（(-24,-20)→(2,27)）为铰链，掀开约 31°
    theta = math.atan2(47, 26)
    lid = _box(bpy, "gp_lid", (50, 45, 2.2), (0, 3.5, 25.1), lid_m, rot=(0, 0, theta))
    d = Vector((26, 47, 0)).normalized()
    h = Vector((-11, 3.5, 24))
    m = Matrix.Translation(h) @ Matrix.Rotation(-0.55, 4, d) @ Matrix.Translation(-h)
    lid.matrix_world = m @ lid.matrix_world

    # 键盘：白键 10 + 黑键 7（示意间距）
    for i in range(10):
        x = -20.7 + 4.6 * i
        _box(bpy, "gp_w_%d" % i, (4.4, 7, 3.5), (x, -23.5, 14), white_m)
    for i, x in enumerate((-18.5, -12.3, -6.2, 0, 6.2, 12.3, 18.5)):
        _box(bpy, "gp_b_%d" % i, (3.2, 4.2, 3.2), (x, -25.2, 16.9), black_m)

    for i, (x, y) in enumerate(((-20, -18), (20, -18), (16, 20))):
        _cyl(bpy, "gp_leg_%d" % i, 2.2, 6, (x, y, 3), leg_m, vertices=16)

    _finish(bpy, "grand_piano")


def build_bonfire(bpy):
    """篝火：8 石环 + 3 斜放柴 + 3 层火苗（橙→黄，赛璐璐硬停段）。"""
    clear_scene(bpy)
    stone_m = toon_material(bpy, "bf_stone", "#b8b4c8", "#7a7690")
    log_m = toon_material(bpy, "bf_log", "#8a5a34", "#5a3a20")
    fire1_m = toon_material(bpy, "bf_fire1", "#ff9a3d", scale_hex("#ff9a3d", 0.7))
    fire2_m = toon_material(bpy, "bf_fire2", "#ffbb63", scale_hex("#ffbb63", 0.7))
    fire3_m = toon_material(bpy, "bf_fire3", "#ffd97a", scale_hex("#ffd97a", 0.7))

    for i in range(8):
        a = i * math.tau / 8
        _cyl(bpy, "bf_stone_%d" % i, 2.6, 4.2,
             (12 * math.cos(a), 12 * math.sin(a), 2.1), stone_m, vertices=10)

    for i, az in enumerate((0.4, 2.1, 3.9)):
        _cyl(bpy, "bf_log_%d" % i, 1.6, 15, (0, 0, 6), log_m,
             rot=(math.radians(50), 0, az))

    _cone(bpy, "bf_fire1", 6.5, 10, (0, 0, 8), fire1_m)
    _cone(bpy, "bf_fire2", 4.5, 9, (0, 0, 13.5), fire2_m)
    _cone(bpy, "bf_fire3", 2.6, 8, (0, 0, 18.5), fire3_m)

    _finish(bpy, "bonfire")


def build_signal_tower(bpy):
    """信号塔：细高塔身 + 平台 + 3 天线 + 2 金色环灯（赛璐璐硬停，非真自发光）。"""
    clear_scene(bpy)
    metal_m = toon_material(bpy, "st_metal", "#6a7a8a", "#445262")
    dark_m = toon_material(bpy, "st_dark", "#445262", scale_hex("#445262", 0.65))
    lamp_m = toon_material(bpy, "st_lamp", "#ffd97a", scale_hex("#ffd97a", 0.65),
                           bright_hex="#ffe9b8")
    ant_m = toon_material(bpy, "st_ant", "#6a7a8a", "#445262")

    _cyl(bpy, "st_base", 8, 3, (0, 0, 1.5), dark_m)
    _cyl(bpy, "st_body", 3, 44, (0, 0, 25), metal_m)
    _cyl(bpy, "st_deck", 6, 2, (0, 0, 47.5), metal_m)

    _torus(bpy, "st_ring1", 5.2, 0.7, (0, 0, 30), lamp_m)
    _torus(bpy, "st_ring2", 4.4, 0.6, (0, 0, 41), lamp_m)

    _cyl(bpy, "st_ant_c", 0.5, 13, (0, 0, 55), ant_m)
    for i, (x, y) in enumerate(((4.2, 0), (0, 4.2), (-4.2, 0))):
        _cyl(bpy, "st_ant_%d" % i, 0.4, 9, (x, y, 52), ant_m)

    _finish(bpy, "signal_tower")


def build_server_rack(bpy):
    """服务器机架：机柜 + 正面 6 条水平缝 + 2 绿色指示灯（赛璐璐硬停）。"""
    clear_scene(bpy)
    cab_m = toon_material(bpy, "sr_cab", "#2a2f3a", "#171b22")
    front_m = toon_material(bpy, "sr_front", "#171b22", scale_hex("#171b22", 0.65))
    slot_m = toon_material(bpy, "sr_slot", "#2a2f3a", "#171b22")
    led_m = toon_material(bpy, "sr_led", "#7dffa8", scale_hex("#7dffa8", 0.6))

    _box(bpy, "sr_cab", (30, 24, 50), (0, 0, 25), cab_m)
    _box(bpy, "sr_front", (30.6, 1.2, 50), (0, -12.6, 25), front_m)
    for i, z in enumerate((10, 17, 24, 31, 38, 45)):
        _box(bpy, "sr_slot_%d" % i, (26, 1.6, 2.6), (0, -13.4, z), slot_m)
    _box(bpy, "sr_led1", (1.4, 1.0, 1.4), (-9, -13.4, 47.5), led_m)
    _box(bpy, "sr_led2", (1.4, 1.0, 1.4), (-6.5, -13.4, 47.5), led_m)

    _finish(bpy, "server_rack")


def build_satellite_dish(bpy):
    """卫星碟：底座 + 立杆 + 下半球碟面（口朝前上方 -Y）+ 馈源杆/馈源头。"""
    clear_scene(bpy)
    metal_m = toon_material(bpy, "sd_metal", "#7ab8d8", scale_hex("#7ab8d8", 0.65))
    dish_m = toon_material(bpy, "sd_dish", "#e8ecf0", "#9aa6b4")
    dark_m = toon_material(bpy, "sd_dark", "#7ab8d8", scale_hex("#7ab8d8", 0.65))

    _cyl(bpy, "sd_base", 7, 4, (0, 0, 2), dark_m)
    _cyl(bpy, "sd_pole", 1.6, 20, (0, 0, 14), metal_m)

    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=12)
    dish = bpy.context.active_object
    dish.name = "sd_dish"
    bm = bmesh.new()
    bm.from_mesh(dish.data)
    for v in list(bm.verts):
        if v.co.z > -0.5:
            bm.verts.remove(v)
    bm.to_mesh(dish.data)
    bm.free()
    dish.data.materials.append(dish_m)
    dish.rotation_euler = (math.radians(32), 0, 0)
    dish.location = (0, -5, 27)

    _rod(bpy, "sd_feed", (0, -1, 22), (0, -10.5, 34.5), 0.5, metal_m)
    _sphere(bpy, "sd_feedge", 1.8, (0, -10.5, 34.5), metal_m)

    _finish(bpy, "satellite_dish")


def _container_parts(bpy, name, color):
    col_m = toon_material(bpy, name + "_shell", color, scale_hex(color, 0.65))
    dark_m = toon_material(bpy, name + "_dark",
                           scale_hex(color, 0.65), scale_hex(color, 0.42))
    _box(bpy, name + "_body", (44, 36, 22), (0, 0, 11), col_m)
    for i, (x, y) in enumerate(((-21, -17), (21, -17), (-21, 17), (21, 17))):
        _box(bpy, "%s_post_%d" % (name, i), (2.6, 2.6, 22.4), (x, y, 11), dark_m)
    for side in (1, -1):
        for j, y in enumerate((-12, -6, 0, 6, 12)):
            _box(bpy, "%s_rib_%d_%d" % (name, side, j), (1.6, 3.2, 22),
                 (side * 22.6, y, 11), dark_m)


def build_container_r(bpy):
    """集装箱（红变体）：箱体 + 4 角立柱 + 两侧竖波纹。"""
    clear_scene(bpy)
    _container_parts(bpy, "container_r", "#e88a9a")
    _finish(bpy, "container_r")


def build_container_b(bpy):
    """集装箱（蓝变体）。"""
    clear_scene(bpy)
    _container_parts(bpy, "container_b", "#7ab8d8")
    _finish(bpy, "container_b")


def build_container_g(bpy):
    """集装箱（绿变体）。"""
    clear_scene(bpy)
    _container_parts(bpy, "container_g", "#8ad8a0")
    _finish(bpy, "container_g")


def build_gantry(bpy):
    """龙门吊：双门架腿 + 横梁 + 警示黄小车 + 吊索与钩。"""
    clear_scene(bpy)
    steel_m = toon_material(bpy, "gn_steel", "#5a7a9a", "#3d5468")
    dark_m = toon_material(bpy, "gn_dark", "#3d5468", scale_hex("#3d5468", 0.65))
    hook_m = toon_material(bpy, "gn_hook", "#3d5468", scale_hex("#3d5468", 0.65))
    trolley_m = toon_material(bpy, "gn_trolley", "#ffd23d", scale_hex("#ffd23d", 0.65))

    for sx in (1, -1):
        _box(bpy, "gn_leg_%d" % sx, (7, 7, 40), (sx * 20, 0, 20), steel_m)
        _box(bpy, "gn_foot_%d" % sx, (9, 9, 3), (sx * 20, 0, 1.5), dark_m)
    _box(bpy, "gn_beam", (56, 6, 6), (0, 0, 43), steel_m)
    _box(bpy, "gn_trolley", (9, 8, 5), (-6, 0, 38.5), trolley_m)
    _rod(bpy, "gn_rope", (-6, 0, 36), (-6, 0, 24), 0.7, hook_m)
    _torus(bpy, "gn_hook", 2.6, 0.7, (-6, 0, 21.5), hook_m, rot=(math.radians(90), 0, 0))

    _finish(bpy, "gantry")


def build_ship(bpy):
    """货轮：尖首船体 + 船楼 + 甲板 2 集装箱，船首朝 -Y。"""
    clear_scene(bpy)
    hull_m = toon_material(bpy, "sh_hull", "#e8e2d8", "#9a9488")
    white_m = toon_material(bpy, "sh_white", "#e8e2d8", "#9a9488")
    red_m = toon_material(bpy, "sh_red", "#d85a4a", scale_hex("#d85a4a", 0.65))
    blue_m = toon_material(bpy, "sh_blue", "#656059", scale_hex("#656059", 0.65))

    outline = [(-14, 25), (14, 25), (14, -18), (0, -28), (-14, -18)]
    _prism(bpy, "sh_hull", outline, 0, 14, hull_m)
    _box(bpy, "sh_deck", (27.6, 42, 1.6), (0, 1, 14.8), white_m)
    _box(bpy, "sh_cabin", (16, 10, 11), (0, 15, 21), white_m)
    _box(bpy, "sh_bridge", (17, 6, 3), (0, 12.5, 28), white_m)
    _box(bpy, "sh_chimney", (4, 5, 4.5), (0, 18, 28.7), red_m)
    _box(bpy, "sh_box1", (11, 8, 5), (0, -6, 18.6), red_m)
    _box(bpy, "sh_box2", (11, 8, 5), (0, -16, 18.6), blue_m)

    _finish(bpy, "ship")


def build_scaffold(bpy):
    """脚手架：3 层钢管网格 + 2 面安全网（赛璐璐禁半透明 → 不透明深橙阴影层）。"""
    clear_scene(bpy)
    pipe_m = toon_material(bpy, "sc_pipe", "#e88a3d", "#a05a24")
    net_m = toon_material(bpy, "sc_net", "#a05a24", scale_hex("#a05a24", 0.65))

    for sx in (1, -1):
        for sy in (1, -1):
            _cyl(bpy, "sc_post_%d%d" % (sx, sy), 0.8, 30,
                 (sx * 12, sy * 10, 15), pipe_m, vertices=10)
    for z in (1, 15.5, 30):
        for sy in (1, -1):
            _rod(bpy, "sc_x_%d_%d" % (z, sy), (-12, sy * 10, z), (12, sy * 10, z),
                 0.7, pipe_m)
        for sx in (1, -1):
            _rod(bpy, "sc_y_%d_%d" % (z, sx), (sx * 12, -10, z), (sx * 12, 10, z),
                 0.7, pipe_m)

    _plane(bpy, "sc_net_front", (24, 30), (0, -10.2, 15.2), net_m,
           rot=(math.radians(90), 0, 0))
    _plane(bpy, "sc_net_side", (20, 30), (12.2, 0, 15.2), net_m,
           rot=(math.radians(90), 0, math.radians(90)))

    _finish(bpy, "scaffold")


def build_crane(bpy):
    """塔吊：交替小立方体塔身 + 水平吊臂/平衡臂 + 配重 + 拉杆 + 小钩。"""
    clear_scene(bpy)
    y1 = toon_material(bpy, "cr_y1", "#ffd23d", "#b8942a")
    y2 = toon_material(bpy, "cr_y2", "#b8942a", scale_hex("#b8942a", 0.65))
    hook_m = toon_material(bpy, "cr_hook", "#6a7a8a", "#445262")

    for i in range(10):
        m = y1 if i % 2 == 0 else y2
        _box(bpy, "cr_mast_%d" % i, (5.2, 5.2, 5),
             (1.2 if i % 2 else -1.2, 0, 2.6 + i * 5.1), m)
    _box(bpy, "cr_cab", (4.6, 4.6, 4.5), (0, 0, 53), y1)
    _box(bpy, "cr_jib", (34, 4, 4), (11, 0, 53.5), y1)
    _box(bpy, "cr_cjib", (13, 4, 4), (-11.5, 0, 53.5), y2)
    _box(bpy, "cr_cw", (5, 6, 7), (-15.5, 0, 58.5), y2)
    _box(bpy, "cr_apex", (2.6, 2.6, 4), (0, 0, 57.5), y1)
    _rod(bpy, "cr_tie1", (0, 0, 59), (26, 0, 54.5), 0.35, hook_m)
    _rod(bpy, "cr_tie2", (0, 0, 59), (-16.5, 0, 54.5), 0.35, hook_m)
    _rod(bpy, "cr_hookrod", (20, 0, 51.5), (20, 0, 40), 0.5, hook_m)
    _torus(bpy, "cr_hook", 2.4, 0.6, (20, 0, 38), hook_m, rot=(math.radians(90), 0, 0))

    _finish(bpy, "crane")


def build_fence(bpy):
    """施工围挡：4 木色面板 + 顶部深色条纹 + 2 端立柱。"""
    clear_scene(bpy)
    panel_m = toon_material(bpy, "fe_panel", "#a87443", "#6e4a2a")
    stripe_m = toon_material(bpy, "fe_stripe", "#6e4a2a", scale_hex("#6e4a2a", 0.65))
    post_m = toon_material(bpy, "fe_post", "#6e4a2a", scale_hex("#6e4a2a", 0.65))

    for i, x in enumerate((-16.1, -5.4, 5.4, 16.1)):
        _box(bpy, "fe_panel_%d" % i, (10.4, 1.0, 13), (x, 0, 7), panel_m)
    _box(bpy, "fe_stripe", (45, 1.3, 2.4), (0, 0, 14.3), stripe_m)
    for sx in (1, -1):
        _box(bpy, "fe_post_%d" % sx, (1.8, 1.8, 16), (sx * 22.5, 0, 8), post_m)

    _finish(bpy, "fence")


def build_key_tile(bpy):
    """琴键砖装饰：6 金键 + 4 深金键地砖（扁平，宽度归一 60，亮部带自发光感 #ffe9b8）。"""
    clear_scene(bpy)
    white_m = toon_material(bpy, "kt_white", "#c9a86a", "#8a6f3a", bright_hex="#ffe9b8")
    black_m = toon_material(bpy, "kt_black", "#8a6f3a", scale_hex("#8a6f3a", 0.65))

    for i, x in enumerate((-16.25, -9.75, -3.25, 3.25, 9.75, 16.25)):
        _box(bpy, "kt_w_%d" % i, (6.3, 16, 2.6), (x, 0, 1.3), white_m)
    for i, x in enumerate((-13, -6.5, 6.5, 13)):
        _box(bpy, "kt_b_%d" % i, (3.9, 9, 5.4), (x, -3.5, 2.7), black_m)

    _finish(bpy, "key_tile")


def _tree_parts(bpy, name):
    trunk_m = toon_material(bpy, name + "_trunk", "#a87443", "#6e4a2a")
    # 三层树冠 深→浅，各层暗部 = 主色再压 30%
    g1 = toon_material(bpy, name + "_g1", "#3f8a34", scale_hex("#3f8a34", 0.7))
    g2 = toon_material(bpy, name + "_g2", "#5aa83a", scale_hex("#5aa83a", 0.7))
    g3 = toon_material(bpy, name + "_g3", "#7ec850", scale_hex("#7ec850", 0.7))

    _cyl(bpy, name + "_trunk", 2.6, 13, (0, 0, 6.5), trunk_m, vertices=10)
    _cone(bpy, name + "_c1", 10.5, 17, (0, 0, 19), g1, vertices=10)
    _cone(bpy, name + "_c2", 8.0, 14, (0, 0, 30.5), g2, vertices=10)
    _cone(bpy, name + "_c3", 5.5, 12, (0, 0, 41.5), g3, vertices=10)


def build_tree_a(bpy):
    """松树（大）：树干 + 3 层深绿→浅绿树冠。"""
    clear_scene(bpy)
    _tree_parts(bpy, "tree_a")
    _finish(bpy, "tree_a")


def build_tree_b(bpy):
    """松树（小）：整体 0.7 倍（归一目标 42 而非 60，保持相对大小）。"""
    clear_scene(bpy)
    _tree_parts(bpy, "tree_b")
    _finish(bpy, "tree_b", target=TARGET_HEIGHT * 0.7)


def build_rock(bpy):
    """岩石：ICO 球细分 1 + 固定种子径向随机位移，灰色低多边形。"""
    clear_scene(bpy)
    rock_m = toon_material(bpy, "rk_rock", "#b8b4c8", "#7a7690")
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=14)
    rock = bpy.context.active_object
    rock.name = "rk_rock"
    rnd = random.Random(20261011)
    for v in rock.data.vertices:
        v.co = v.co * (1.0 + rnd.uniform(-0.26, 0.30))
    rock.data.materials.append(rock_m)
    rock.location = (0, 0, 11)

    _finish(bpy, "rock")


def build_sign(bpy):
    """路标牌：立柱 + 横向双木板面板 + 上下金色薄条（赛璐璐硬停，亮部 #ffe9b8）。"""
    clear_scene(bpy)
    post_m = toon_material(bpy, "sg_post", "#8a5f34", scale_hex("#8a5f34", 0.65))
    panel_m = toon_material(bpy, "sg_panel", "#c8945e", "#8a5f34")
    gold_m = toon_material(bpy, "sg_gold", "#ffd97a", scale_hex("#ffd97a", 0.65),
                           bright_hex="#ffe9b8")

    _cyl(bpy, "sg_post", 1.6, 30, (0, 0, 15), post_m)
    _box(bpy, "sg_p1", (21, 1.6, 8), (9, 0, 25), panel_m)
    _box(bpy, "sg_p2", (16, 1.6, 6), (7, 0, 16.5), panel_m)
    _box(bpy, "sg_g1t", (21.8, 2.0, 1.1), (9, 0, 29.0), gold_m)
    _box(bpy, "sg_g1b", (21.8, 2.0, 1.1), (9, 0, 21.0), gold_m)
    _box(bpy, "sg_g2t", (16.8, 2.0, 1.1), (7, 0, 19.5), gold_m)
    _box(bpy, "sg_g2b", (16.8, 2.0, 1.1), (7, 0, 13.5), gold_m)

    _finish(bpy, "sign")


def build_dock(bpy):
    """码头桩：4 木桩 + 2 块横向木板。"""
    clear_scene(bpy)
    wood_m = toon_material(bpy, "dk_wood", "#a87443", "#7a4f2e")
    wood2_m = toon_material(bpy, "dk_wood2", "#c8945e", "#7a4f2e")

    for i, (x, y) in enumerate(((-10, -5.5), (10, -5.5), (-10, 5.5), (10, 5.5))):
        _cyl(bpy, "dk_post_%d" % i, 2.4, 15, (x, y, 7.5), wood_m, vertices=12)
    _box(bpy, "dk_plank1", (28, 4.6, 2.4), (0, -5.5, 16.2), wood2_m)
    _box(bpy, "dk_plank2", (28, 4.6, 2.4), (0, 5.5, 16.2), wood_m)

    _finish(bpy, "dock")
