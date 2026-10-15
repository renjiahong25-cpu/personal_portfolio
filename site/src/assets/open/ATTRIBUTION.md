# Open Asset Attribution / 开源素材授权清单

> 星露谷风格等距群岛站点（瓦片 64×32，投影 `(gx-gy)*32, (gx+gy)*16`）
> 下载日期：2026-10-12（所有下载均保留原始 zip 于 `kenney-licenses/`）

## 1. Kenney（kenney.nl）— CC0 公共领域

以下素材均由 Kenney (kenney.nl) 创作，许可为 **Creative Commons CC0 1.0**（https://creativecommons.org/publicdomain/zero/1.0/ ），
可自由用于个人/商业项目，无需署名（以下为自愿致谢）。每个解压目录内均含官方 `License.txt` 原件。

| 素材包 | 目录 | 版本 | zip 原件（kenney-licenses/） | 直链（kenney.nl） |
|---|---|---|---|---|
| Tiny Farm | `kenney-tiny-farm/` | 1.0 (2026-07-01) | `kenney_tiny-farm.zip` (187 KB) | https://kenney.nl/assets/tiny-farm |
| Tiny Town | `kenney-tiny-town/` | 1.1 (2025-01-01) | `kenney_tiny-town.zip` (178 KB) | https://kenney.nl/assets/tiny-town |
| Isometric Tiles Landscape | `kenney-iso-landscape/` | 1.0 (2014-12-04) | `kenney_isometric-landscape.zip` (1.1 MB) | https://kenney.nl/assets/isometric-tiles-landscape |
| Isometric Tiles City | `kenney-iso-city/` | 1.0 (2014-12-05) | `kenney_isometric-city.zip` (967 KB) | https://kenney.nl/assets/isometric-tiles-city |
| Isometric Miniature Farm | `kenney-iso-mini-farm/` | 2.0 (2019-02-22) | `kenney_isometric-miniature-farm.zip` (9.7 MB) | https://kenney.nl/assets/isometric-miniature-farm |
| Isometric Tiles Vehicles | `kenney-iso-vehicles/` | 1.0 (2014-12-20) | `kenney_isometric-vehicles.zip` (2.2 MB) | https://kenney.nl/assets/isometric-tiles-vehicles |

致谢（非强制）：Thanks to Kenney and https://kenney.nl for the CC0 game assets.

说明：
- 任务原计划中的 "Tiny Village" 与 "Tiny Wildlife" 在 kenney.nl 不存在（页面 404，Tiny 系列仅有 Farm/Town/Battle/Ski/Dungeon/Factory 六包）。
  已用同系列的 **Tiny Town**（等距村庄需求：房屋/道路/路灯/长椅）替代 Tiny Village；无对应野生动物包（Tiny Farm 内含农场动物，但为俯视图）。
- **Tiny Farm / Tiny Town 经逐像素几何验证为 16×16 俯视图（top-down），不是 2:1 等距**，不能直接用于本站等距网格；
  Kenney 真正的 CC0 等距包为上表 4 个 Isometric 系列包（isometric 标签页 https://kenney.nl/assets?search=isometric 可查）。

## 2. OpenGameArt.org — CC-BY（**必须署名**）

| 素材 | 目录 | 作者 | 许可 | 署名要求（原文） | 来源页 |
|---|---|---|---|---|---|
| Isometric Simple Wooden Cart（2 张 1600×1200 sheet，各 16 向等距木制手推车，透明底；已拆分为 `hjm_parked_00..15.png` / `hjm_pulled_00..15.png`） | `cart-candidates/hjm-cart_*.png` | Varkalandar（Hansjörg Malthaner） | CC-BY 4.0 / CC-BY 3.0 / OGA-BY 3.0（多许可） | "For attribution, please state my name, **Hansjörg Malthaner**, and link here: http://opengameart.org/users/varkalandar" | https://opengameart.org/content/isometric-simple-wooden-cart |
| 80 More Isometric Trees（81 棵等距树，40 型 × 2 变体 + 1 额外变体，透明底 2:1 投影；入选 8 棵拆为 `tree_01..08_src.png` → curated `tree_01..08.png`，形状含圆冠/层叠冠/瘦高/矮胖 + 红叶/黄叶点缀） | `trees/tree_0?_src.png`（7z 原件 `hjm-more_trees.7z` 4.6 MB；页面证据 `evidence_80-more-isometric-trees.html`、预览 `evidence_hjm-trees_preview.jpg`；选树依据 `metrics_80more.json` + 联系表 `contact_80more.png`） | Varkalandar（Hansjörg Malthaner） | CC-BY 4.0 / CC-BY-SA 4.0（双许可，本站采用 CC-BY 4.0） | "For attribution, please state my name, **Hansjörg Malthaner**, and link here: http://opengameart.org/users/varkalandar" | https://opengameart.org/content/80-more-isometric-trees |
| Medieval Cart Prop（等距 16 向木制马车 551×320 + 独立阴影层 16 张，`cart_*.png` / `shadow_cart_*.png`） | `cart-candidates/medieval_cart/`（zip 原件 `medieval_cart_isometric.zip` 3.4 MB） | Bleed | CC-BY 4.0 | "Bleed's Game Art - https://www.patreon.com/bleed" | https://opengameart.org/content/medieval-cart-prop |

## 3. 未使用 / 不合格候选记录

- Kenney Isometric Tiles Vehicles：仅现代车辆（Sedan/Pick-up/Truck/Ambulance/Police/Taxi/Garbage，16 向 × 多色），**无木制手推车**。
- OpenGameArt 搜索 "isometric cart" 其余结果（LPC Mine Carts and Tracks 等为俯视图矿车）不符合"等距木制手推车"要求，未下载。
- 等距树搜索（OpenGameArt "isometric tree" 评论数排序）：
  - **64x128 Isometric Tiles: Grassland Seasons**（formconstant）：许可仅 **CC-BY-SA 4.0**（share-alike 会向本项目传导 SA 义务，超出 CC0/CC-BY 范围）→ 未采用。 https://opengameart.org/content/64x128-isometric-tiles-grassland-seasons
  - **Tree Collection v2.6**（Bleed，CC-BY 3.0，112 棵）：阴影烘焙在 sprite 内（作者声明源文件已丢失、无法分离阴影层），且为 3D 渲染风格，与站点卡通像素风不一致 → 未采用。 https://opengameart.org/content/tree-collection-v26-bleeds-game-art
  - **Isometric birch/beech tree、Isometric Small Trees、Isometric dead trees**（均为 Varkalandar 旧作，2017）：作者 2020 年自述"old trees didn't seem good enough"并发布了 80 More Isometric Trees，故选用后者，旧作未下载。
  - kenney.nl isometric 标签页（14 页）：未下载的等距包均为 3D 系（Mini/City 等），无新的 2D 等距树素材；与既有逐图验证一致，**Kenney 无等距树**。
