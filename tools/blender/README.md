# Blender 5.2 headless 等距道具渲染管线

把程序化 3D 道具渲染成 2D 等距 spritesheet（8 方向），供 Vue3+PixiJS 作品集站点消费。

## 运行

```powershell
# 1) 引擎探测（确认 Blender 版本与可用渲染引擎 id）
& "D:\blender\blender.exe" --background --python probe.py

# 2) 全量构建（当前 21 个道具，512px × 8 方向）
& "D:\blender\blender.exe" --background --python build.py
```

产物在 `out/`，每道具 3 个文件：`<name>.png` / `<name>.webp`（8×1 spritesheet
4096×512，webp quality=90，png 兜底）+ `<name>.json`（manifest，见契约）。

单道具渲染耗时约 2.6–11s（EEVEE，512px，8 帧；21 道具全量约 1 分钟）。
若 EEVEE 在该环境异常，管线自动回退 CYCLES（CPU 32 samples），速度会明显变慢。
单个道具失败不会中断整批（`PROP_FAIL` + traceback 打印，末尾汇总 `failures`）。

## 道具清单

归一化后尺寸（宽×深×高，世界单位；最大边 = 60，tree_b 按约定 0.7 倍 = 42）：

| 道具 | 尺寸 W×D×H | 变体 | 说明 |
| --- | --- | --- | --- |
| `cart` | 51×42×60 | — | 小推车（既有） |
| `tent` | 37×46×60 | — | 帐篷（既有） |
| `grand_piano` | 53×60×27 | — | 三角钢琴：琴身+掀盖+10 白/7 黑键+3 腿 |
| `bonfire` | 60×60×46 | — | 篝火：8 石环+3 柴+3 层自发光火苗 |
| `signal_tower` | 16×16×60 | — | 信号塔：塔身+3 天线+2 金色自发光环灯 |
| `server_rack` | 37×31×60 | — | 服务器机架：6 水平缝+2 绿色指示灯 |
| `satellite_dish` | 39×42×60 | — | 卫星碟：底座+杆+半球碟+馈源 |
| `container_r` | 60×47×29 | 3 色 | 集装箱（红）：4 角立柱+两侧竖波纹 |
| `container_b` | 60×47×29 | 3 色 | 集装箱（蓝） |
| `container_g` | 60×47×29 | 3 色 | 集装箱（绿） |
| `gantry` | 60×10×49 | — | 龙门吊：门架+横梁+小车+吊钩 |
| `ship` | 32×60×35 | — | 货轮：尖首船体+船楼+甲板 2 集装箱 |
| `scaffold` | 50×42×60 | — | 脚手架：3 层钢管+蓝色半透明安全网(α0.55) |
| `crane` | 45×6×60 | — | 塔吊：交替小立方体塔身+吊臂+配重+钩 |
| `fence` | 60×2×21 | — | 施工围挡：4 蓝板+黄顶条+2 立柱 |
| `key_tile` | 60×25×8 | — | 琴键砖：6 白+4 黑扁平地砖 |
| `tree_a` | 25×27×60 | 2 尺寸 | 松树（大）：树干+3 层深→浅绿树冠 |
| `tree_b` | 18×19×42 | 2 尺寸 | 松树（小）：整体 0.7 倍；manifest `size=[45,45]`（游戏内 0.7×64px） |
| `rock` | 49×55×60 | — | 岩石：ICO 球细分 1+固定种子顶点位移 |
| `sign` | 44×6×60 | — | 路标牌：立柱+双深色面板+金色描边薄条 |
| `dock` | 60×34×37 | — | 码头桩：4 木桩+2 横木板 |

## 参数约定

- 相机：正交投影，`rotation_euler = (54.736°, 0, yaw)`，yaw = dir × 45°（真等距）。
- `ortho_scale = 2.81 × max(bbox 边长)`（行业常数 2.81 ≈ 2√2，保证任意朝向不裁切；
  道具归一化到 60 单位时 ortho_scale = 168.6）。
- 归一化对象 = **bbox 最大边**（`_finish(target)`）：高主导道具即"高 60"（cart/tent 与
  旧纯高度归一产物完全一致）；扁宽道具（key_tile/fence/dock/gantry/ship/crane 等）
  按宽度归一，保证 ortho_scale 恒定且道具在 64px tile 内占地合理。变体可用
  `target` 参数（tree_b = 42 = 0.7×60）。
- 锚点取景：**实际几何**（网格顶点）投影后的最低点——离相机最近的车轮底/地面角——
  落在画面底边正中。注意不能用 bbox 角点：车斗底角是车轮之间的空点，会导致道具悬空
  （实测 cart 悬空 11px），顶点法实测 16/16 tile `bottom_gap=0`。
- 每道具 8 方向 orbit，每方向 1 帧（N 帧动画预留：`frames` 参数沿 sheet 行方向扩展）。
- 512×512/方向，`film_transparent` 透明背景，PNG RGBA 8bit。
- 道具正面统一朝 -Y（方向 0 相机从 -Y 侧观察）。

## manifest 契约（`<prop>.json`）

```json
{
  "prop": "cart",
  "dirs": 8,
  "frames": 1,
  "size": [64, 64],
  "anchor": [32, 64],
  "fps": 8,
  "sheet": [4096, 512],
  "res": 512
}
```

- `dirs`：sheet 列数（方向数，左→右 = 方向 0→7，每 45°）
- `frames`：sheet 行数（每方向帧数，首版均为 1，预留动画）
- `size`：游戏内像素尺寸（站点按 64×64 tile 使用，512 源图供降采样）。默认 64；
  尺寸变体按实际游戏像素给出（tree_b 小松树 = 45）
- `anchor`：`[32, 64]` = tile 底边中心，对应道具占地最近角点（脚底），
  PixiJS 放置时用 `sprite.anchor`（按 size 归一后即 (0.5, 1.0)）对齐即可；
  tree_b 为 `[22, 45]`
- `fps`：动画帧率预留（8）
- `sheet`/`res`：源图信息，降采样用（LANCZOS + alpha 阈值 128，参考
  craigrmccown/iso-render-pipeline 的 downsample 做法）

## 文件职责

- `probe.py`：打印 Blender 版本、内置 Python/PIL 状态、可用渲染引擎 id（5.2 探测结果：
  引擎枚举为 `BLENDER_EEVEE` / `BLENDER_WORKBENCH` / `CYCLES`，EEVEE Next 在 5.2 里
  的 id 就是 `BLENDER_EEVEE`；`RenderEngine.__subclasses__()` 在 5.2 不可用）
- `probe2.py`：5.2 材质 API 探测（Principled BSDF socket 全表、`surface_render_method`
  枚举 = DITHERED/BLENDED、blend_method 仍存在）
- `props.py`：程序化建模（21 个 build_*），清场→建件→父化根 Empty→最大边归一 60 单位，
  根对象名 = 道具名；含基本体助手（_box/_cyl/_cone/_sphere/_torus/_plane/_rod/_prism）、
  自发光（make_emit）与半透明（set_alpha，EEVEE Next 5.2 的
  `surface_render_method="BLENDED"` + BSDF Alpha）
- `render_props.py`：核心管线 `render_prop()`：相机/灯光/透明设置 → 8 方向逐帧渲染 →
  子进程调系统 Python(Pillow) 拼 sheet → 写 webp+png+manifest；引擎自动选择与
  CYCLES 回退
- `compose_sheet.py`：Pillow 拼 sheet / 存 webp(quality 90) / 写 manifest
  （Blender 5.2 内置 Python 3.13 无 PIL，故走系统 Python `D:\miniconda3\python.exe`）
- `build.py`：入口，`PROPS` 列表（21 项，含 container_r/b/g 与 tree_a/b 变体）；
  单道具异常不中断整批，末尾汇总 failures
- `verify_sheet.py`：像素级自检（逐方向不透明包围盒 / 底边锚定 / 边缘黑边 / 对称性），
  `python verify_sheet.py cart`

## 扩展新道具

1. 在 `props.py` 写 `build_<name>(bpy)`：`clear_scene` → 基本体建件 → `_finish(bpy, name)`
   （自动命名根 Empty、归一高度 60、地面 z=0）。
2. 在 `build.py` 的 `PROPS` 加一行 `("<name>", build_<name>)`。
3. 跑 `build.py`，用图像查看器检查 `out/<name>.png`（8 方向完整、透明底、脚底对齐
   tile 底边）。
4. 站点侧：`fetch out/<name>.webp` + 读 `<name>.json`，按 `size` 缩放到 64px，
   `anchor` 对齐放置。

## Blender 授权说明

本管线使用 `D:\blender\blender.exe`（Blender 5.2.0 LTS，**个人授权**安装）。
仅在本机 headless 渲染，产物（PNG/WebP/JSON）为自有项目素材；Blender 本体及其
默认资产不随本项目分发。若更换机器，需按 Blender 授权条款重新获取/安装。

## 已知风险（Blender 5.2 API 差异，均已在本管线内消化）

- `bpy.types.RenderEngine.__subclasses__()` 返回的类无 `bl_idname`（probe 实测报错），
  枚举引擎改用 `scene.render.engine` 赋值试错（`available_engines()`）。
- EEVEE Next 合并进主引擎后 id 为 `BLENDER_EEVEE`（`BLENDER_EEVEE_NEXT` 已不存在）。
- 内置 Python 3.13 不带 PIL → 拼 sheet 走外部子进程。
- `view_transform` 用 `Standard`（4.x+ 默认 AgX 会发灰、影响素材颜色一致性）。
  **Standard 无色调映射**：漫反射 albedo×光照 >1.0 直接 clipping 成纯白（实测初版
  KeySun 3.0 + 环境 1.0 全白），灯光能量必须压低（1.3 / 0.45 / 0.55）。
- 节点名与 socket 名会随 Blender 界面语言本地化：`nodes.get("Principled BSDF")`、
  `inputs["Base Color"]` 都可能取不到（实测材质全落回 0.8 默认灰）。一律按
  `node.type == "BSDF_PRINCIPLED"` / identifier 多候选匹配（`props._sock`）。
- `blender -b -P build.py` 不把脚本目录加入 `sys.path`，build.py 需显式
  `sys.path.insert(0, ROOT)`。
- 4.2+ 起 `Mesh.vertices` 无 `.remove()`（bpy_prop_collection 移除该 API），删顶点
  走 bmesh（`bm.verts.remove(v)`，见 satellite_dish 的半球碟）。
- 半透明：5.2 的 EEVEE Next 用 `material.surface_render_method = "BLENDED"`
  （枚举仅 DITHERED/BLENDED）+ BSDF `Alpha` 输入；旧 `blend_method` 仍存在但
  一并不设（props.set_alpha 双写，try/except 兜底）。

## 验证记录

### 首版（cart/tent）

- probe：Blender 5.2.0 LTS，引擎枚举 `['BLENDER_EEVEE','BLENDER_WORKBENCH','CYCLES']`
- 耗时：cart 7.2s / tent 3.2s（EEVEE，512px×8 方向，远低于 2min 预算）
- 像素级验证：16/16 tile `bottom_gap=0`；反向方向严格镜像对称（如 dir00 cx=322.5
  ↔ dir04 cx=188.5）；边缘亮度 105–164 无黑边；四角 alpha=0 透明背景；
  颜色带：cart 顶=红篷 (180,108,89) / 中=木斗 (152,112,91)，tent=帆布 (174,159,133)
- manifest：`json.load` 合法，键与契约一致

### 21 道具全量（2026-10-11）

- 渲染：21/21 成功（EEVEE），单道具 2.6–11.0s（scaffold 含半透明网较慢），全量约 1 分钟
- 像素级验证（verify_sheet.py：8 方向非空 + `bottom_gap<=6` + 四角透明 + 边缘无黑边）：
  **21/21 PASS**，168/168 tile `bottom_gap=0`，edge_luma 69–217 无黑边
- cx 说明：锚点是"离相机最近的地面角点"（投影最低顶点）落在 tile 底边正中，非对称
  道具（cart/crane/ship 等）bbox 中心天然偏离 256 属预期（cart dir00 cx=322.5 ↔
  dir04 cx=188.5 镜像），verify_sheet 已按此口径不再对 cx 设阈值
- 尺寸：最大边全部 = 60（tree_b = 42，0.7 倍变体，见"道具清单"）

## 站点消费

`out/` 下全部 `<name>.png/.webp/.json` 平铺复制到
`D:\Program Files\supplychainpolicyagent\site\src\assets\props\`（无子目录），
站点构建直接按 manifest（`size=[64,64]`、`anchor=[32,64]`）加载。
