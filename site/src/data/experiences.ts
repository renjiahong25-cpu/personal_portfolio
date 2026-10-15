export interface Experience { id: string; title: string; theme: string; problem: string; actions: string[]; result: string; glyph: string }
export const EXPERIENCES: Experience[] = [
  { id:'research', title:'让远处的小目标被看见', theme:'研究与实验', problem:'复杂施工背景中的微小人物容易漏检。', actions:['研究注意力与检测方法','承担软件实现、初稿写作、形式分析和可视化','连接检测与场景风险判断'], result:'研究工作已发表，积累了从实验、实现到研究表达的实践。', glyph:'◈' },
  { id:'vision', title:'把巡检经验变成评测闭环', theme:'多模态产品实践', problem:'人工巡检耗时，模型错误需要可定位、可复测。', actions:['设计视觉识别模块与评测指标','使用 LoRA 进行视觉模型微调','追踪 bad case 并推动试点迭代'], result:'形成离线评测、错误分析和试点验证流程。', glyph:'◎' },
  { id:'food', title:'上线只是产品故事的开场', theme:'用户与运营', problem:'餐饮服务的下单、配送和留存体验需要优化。', actions:['推进需求、原型、开发与评审','分析埋点和交互流失节点','协调团队迭代下单与配送流程'], result:'从概念验证推进到上线与留存优化。', glyph:'◒' },
  { id:'logistics', title:'让材料在正确的时刻到达', theme:'供应与调度', problem:'施工高峰需要协调材料进场、运输和仓储。', actions:['参与采购、调度、仓储与末端配送','编制材料需求与进场排期','设置供应预警与动态调配方案'], result:'形成围绕现场供给的排期与协调实践。', glyph:'▣' },
  { id:'sandbox', title:'让 NPC 拥有自己的选择', theme:'AI 游戏原型', problem:'固定脚本难以支持连贯人格与动态叙事。', actions:['设计人格、财富、关系和社会规则系统','以 Qwen 连接对话、推理、行为与渲染','用 Godot 构建 2D 原型并追踪角色一致性'], result:'完成 AI 沙盒 NPC 核心原型与行为评测探索。', glyph:'✦' },
  { id:'webagent', title:'把一次回答连成完整工作流', theme:'Agent 与工具', problem:'网页对话与实际工具执行之间缺少稳定衔接。', actions:['在现有开源框架基础上建设浏览器和工具桥接','约束输出格式与工具范围','解析请求、执行工具、回传结果'], result:'形成可复用的闭环工作流。', glyph:'⌘' },
  { id:'policy', title:'让规则有出处，让回答可追溯', theme:'检索与业务系统', problem:'政策查询、归类与报价需要同时考虑证据和业务流程。', actions:['组合向量检索、关键词检索、RRF 与重排','建设问答、HS预测、政策采集、知识库与离线评测','以流式回答、引用与前端形成交互闭环'], result:'实现可运行模块与检索评测流程。', glyph:'⌕' },
  { id:'iteration', title:'把作品继续打磨到能用', theme:'当前项目', problem:'转录听感、持续陪伴和可体验展示需要真实链路验证。', actions:['PianoFlow 区分忠实转录与可弹改编','桌宠按应用、任务、游戏和场景积累记忆','作品集用真实使用视频展示项目'], result:'持续开发中的工程实践。', glyph:'♮' },
  { id:'innovation', title:'把想法变成可以讨论的方案', theme:'创新与协作', problem:'技术方案需要经过实验、表达与评审。', actions:['参与科研成果与发明方案设计','参加创新竞赛与现场答辩','组织活动并协调多人协作'], result:'连接研究、方案表达与多人协作。', glyph:'◇' }
]
