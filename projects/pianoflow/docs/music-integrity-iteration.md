# PianoFlow 音乐完整性迭代

本轮修复服务于两条明确路径：纯钢琴逐音转录；歌曲人声旋律到钢琴改编。没有声称模型能达到逐音无误的识别精度，也没有声称《盲选》《坏翅膀》《星辰大海》都已通过主观听感验收。

## 实现

- 节拍相位按 `秒 × 采样率 / hop` 转帧；采样率必须显式传入，音频及节拍参数验证拒绝非正或非有限值。
- 忠实转录与 `as-played` 均不做手跨度折音、难度删音、旋律八度折叠、生成伴奏。谱面仍按十六分音符量化，音频/键盘时间轴和 MIDI 使用独立的原始演奏时间；起音和时长不因记谱量化而改变。音源是否原本有某音符仍取决于转录模型。
- Basic Pitch 的最短音长从 11 帧（约 128 ms）改为 4 帧（约 46 ms），避免整类真实快音在形成音符前就被丢弃。
- 改编旋律在全曲范围选一个统一八度偏移，保留全部相邻音程和调外半音。舒适音域是目标中心，不是逐音折叠的硬限制；难度控制伴奏/填充，不删除旋律中的音节与经过音。仅删除同时满足很短、远离邻音、能量不到邻音四分之一的孤立异常音。
- 左手逐个实际和弦事件生成并在事件结束处截断；低音和上方和弦音使用分开的音域与八度候选，尽量平稳连接。和弦填充不会持有到下一和弦，改编试听的踏板按和弦变化抬起，避免一小节内 C→G 仍整节弹 G、旧和弦被踏板混进新和弦。
- 改编 API/界面默认 `melodySource=vocal`：本地 Demucs 分离人声，再用 RMVPE 提取 F0。也可明确选择 `vocal_stem`（用户上传已分离人声）或 `mixed`（实验性混音高声部）。缺资源/模型失败明确报错，没有静默 fallback。混音输出携带 warning；VocalMelody 默认保留调外半音。F0 阶段不强制加载 Whisper，段落 ASR 仅加载已缓存本地模型。

## 本机资源配置

仅引用原资源，不复制模型或更改原项目；不要将 cookies 或私人配置放入交付。所有 cache、log、temp、Numba cache 应设置到副本可写目录。

```powershell
$env:PIANOFLOW_RESOURCE_ROOT = 'D:\Program Files\pianoflow'
$env:PIANOFLOW_BASIC_PITCH_MODEL_PATH = 'D:\Program Files\pianoflow\models\basic_pitch\nmp.onnx'
$env:PIANOFLOW_ML_PYTHON = 'D:\conda_envs\pop2piano\python.exe'
$env:PIANOFLOW_SEPARATOR_PYTHON = 'D:\conda_envs\transkun\python.exe'
$env:PIANOFLOW_DEMUCS_CHECKPOINT = 'D:\Program Files\pianoflow\models\demucs\htdemucs_6s.th'
$env:PIANOFLOW_FLUIDSYNTH_LOCATION = 'D:\Program Files\pianoflow\tools\fluidsynth\bin'
$env:PIANOFLOW_SOUNDFONT_PATH = 'D:\Program Files\pianoflow\models\soundfonts\MuseScore_General.sf2'
```

Demucs 的 `.th` 为 safetensors：固定 HTDemucs 类，根据本地 metadata 还原参数并 `strict=True` 加载权重；不任意导入 metadata 中的可执行类，也不尝试联网下载。两个独立 ML 环境分别具有 Demucs 和 RMVPE 所需库。

Windows 沙箱下 NumPy/Librosa 的 Numba 缓存可能在只读 conda 目录循环尝试创建临时文件；设置 `NUMBA_CACHE_DIR` 到副本可写目录，`TEMP`/`TMP` 同样设置。不要用 `NUMBA_DISABLE_JIT` 替代此设置，它会破坏 librosa 的 gufunc 编译。前端若 Vite CLI 在配置打包时扫描不可读祖先目录，可用 `node scripts/frontend-build.mjs` 的 `configFile:false` API 构建。

## 开发自查

`test_music_integrity.py` 的 12 项新回归检验跨采样率相位、旋律音程/半音、快音清理、同小节和弦变化、两个忠实模式的 MIDI 原始起音/时长、默认人声选项、资源不可用的明确错误，以及和弦填充/踏板在换和弦前释放。音乐回归组覆盖 grid / arranger / faithful / vocal / score / integrity。旧测试中要求逐音折八度、as-played 删音的断言已按新契约更新。完整仓库检查和独立测试需另行报告，未用这些局部测试推断全部应用可靠。

`scripts/make_quality_demo.py --output <directory>` 可重建原创 8 秒短句：source、before-policy、after-policy 的 WAV / MIDI / MusicXML，以及真实 Basic Pitch → faithful score → MIDI/试听的 roundtrip 和 JSON 报告。before-policy 仅重现旧逐音折叠与中点和弦两项错误，不是整个旧版本的基准。一次实际运行：原始 27 音，检测 28 音，100 ms 容差匹配 23 音，recall 85.19%，precision 82.14%，匹配音符平均起音误差 13.70 ms。这说明后处理保留已检测音符，并不说明模型本身不漏音。

本地已缓存《星辰大海》录音的一段 18.375 秒片段（源采样率 48 kHz，约 41.344–59.719 秒）用于私有真实流水线检查。Demucs → RMVPE → 编排/MusicXML/MIDI/FluidSynth 全曲、旋律、左手三轨运行得到 55 个旋律音、92 个谱面音，保留 8 个调外音，没有混音 fallback，冻结源码运行 53.40 秒。该结果只能证明链路可运行，不是人工逐音标注或听感通过；缓存歌曲、裁剪文件、人声与派生试听只留在私有验证目录，不进入交付或公开站点。《盲选》《坏翅膀》没有指定音源，没有宣称完成回归。

## 尚需验收

独立对抗测试后补充修复：忠实转录末攻击重新分桶时钳制回最后真实小节，避免音符再次量化后超出小节集合；Basic Pitch 对末尾不足一个步长的片段补零建立窗口，并将检测时长裁到真实音频末尾。合并推测/模型起音出现双帧平顶时使用 plateau-aware 峰值检测，真实 2.5 秒样本在 2.1 秒开始的 E4 可以被识别。

难度密度先扣除全部旋律，再分配伴奏与填充；伴奏裁剪先覆盖每个和弦事件，避免预算全部落在首个和弦。若原旋律已经超过整体预算，全部支持层静音，并在 `arrangement.densityReport` 与界面显示超预算提示；保留旋律不等于承诺初级难度可弹。两个上传函数现在都在 multipart 中添加真实 audio 文件。新增 `test_music_boundaries.py` 共 20 个边界回归（含真实 ONNX 尾音），`frontend/tests/api-upload.test.mjs` 验证四种上传路径的文件名、字节与浏览器 multipart boundary。

冻结自查：八个音乐测试模块共 174 项，173 通过、1 跳过（旧 graph-contract 测试直接检查副本 `models/`，没有复制权重所以跳过；新增尾音和已有实际 ONNX 多音识别测试均通过）。四个上传检查、12 个 OSMD 高亮检查通过，修改文件 Ruff 通过，前端生产构建通过。独立验收结果由独立测试方另行报告。

歌曲人声分离可能有器乐泄漏，RMVPE 会有八度错误；本轮保留半音避免强制大调化，但不能把分离结果当作乐谱真值。人工试听应检查主旋律是否跟随人声、歌词起音、快音、和弦切换和低音清晰度。节拍为均匀速度估计，不支持充分准确的自由速度、复拍子或全曲速度变化；谱面量化可能合并近邻快音，忠实 MIDI/试听时间仍被保留。没有版权歌逐音 ground truth，因此不声明音乐质量目标已全面完成。
