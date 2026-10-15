export interface ProjectInfo {
  id: string
  name: string
  tagline: string
  taglineEn: string
  desc: string
  tech: string[]
  status: 'live' | 'progress' | 'wip'
  statusText: string
  url: string | null
  urlLabel: string
  accent?: string
  architecture?: string[]
  strengths?: string[]
}

const BASE = 'https://github.com/renjiahong25-cpu/personal_portfolio/tree/main'

export const PROJECTS: Record<string, ProjectInfo> = {
  HUB: {
    id: 'HUB',
    name: 'web-agent',
    tagline: '多AI网页会话协作',
    taglineEn: 'Multi-AI Web Session Collaboration',
    desc: '基于 openteam 的 fork，探索浏览器中的多 AI 协作。通过任务分工、共享会话与过程记录，把网页操作组织成可追踪的协作流程。',
    tech: ['Node.js', 'TypeScript', 'Multi-Agent', '浏览器自动化'],
    status: 'live',
    statusText: '协作原型',
    url: `${BASE}/projects/web-agent`,
    urlLabel: 'GitHub', accent: '#80dced',
    architecture: ['用户任务', '任务编排 / 角色分工', '共享浏览器会话', '操作记录 / 人工确认'],
    strengths: ['将复杂任务拆成可观察的角色分工', '明确继承 openteam，展示二次开发边界']
  },
  PIANO: {
    id: 'PIANO',
    name: 'PianoFlow',
    tagline: '本地AI音乐工作台',
    taglineEn: 'Local AI Music Workbench',
    desc: '开发迭代中的本地音乐工作台：保留原曲音符时间线，从音频转录到 MIDI、播放与乐谱。器乐转录使用 basic-pitch / ONNX；流行歌曲处理探索 Demucs 声部分离与 RMVPE 音高提取。',
    tech: ['Python', 'Tornado', 'basic-pitch / ONNX', 'Demucs / RMVPE', 'Vue3', 'OSMD'],
    status: 'progress',
    statusText: '开发迭代中 · 本地转录与播放',
    url: `${BASE}/projects/pianoflow`,
    urlLabel: 'GitHub', accent: '#f0ca87',
    architecture: ['音频输入 / 原始时间线', '器乐：basic-pitch / ONNX', '流行歌曲：Demucs / RMVPE', '忠实音符时间线 / MIDI', 'Vue 播放与 OSMD 乐谱'],
    strengths: ['保留音符时序，连接转录、MIDI 与播放', '按器乐与流行歌曲区分音频处理链路']
  },
  JOURNEY: {
    id: 'JOURNEY',
    name: 'venue-scout',
    tagline: '团建场地智能规划',
    taglineEn: 'Team-Building Venue Scout',
    desc: '团建场地与行程规划工具：先用确定性规则和约束求解处理人数、预算、活动与时间，再由 LLM 增强说明和备选建议。',
    tech: ['Python', 'OR-Tools', '确定性规划', 'LLM 增强', 'Excel 报告'],
    status: 'live',
    statusText: '规划工具',
    url: `${BASE}/projects/venue-scout`,
    urlLabel: 'GitHub', accent: '#a4dca8',
    architecture: ['场地信息 / 用户约束', '确定性筛选与评分', 'OR-Tools 约束规划', 'LLM 说明增强', '行程 / Excel 报告'],
    strengths: ['将硬约束与语言模型增强分开', '规划结果与预算、人数、天气条件关联']
  },
  TARIFF: {
    id: 'TARIFF',
    name: 'supplychainpolicyagent',
    tagline: '跨境关税与政策智能体',
    taglineEn: 'Cross-border Tariff & Policy Agent',
    desc: '跨境贸易政策智能体：政策采集与结构化、混合检索知识库、关税与合规问答、带评测回归的规则层。',
    tech: ['Python / FastAPI', 'Vue3', 'MySQL', 'Redis', 'MinIO', 'Milvus'],
    status: 'live',
    statusText: '政策检索工作台',
    url: `${BASE}/projects/supplychainpolicyagent`,
    urlLabel: 'GitHub', accent: '#edb978',
    architecture: ['Vue 查询工作台', 'Python / FastAPI API', 'MySQL 数据 / Redis 缓存', 'MinIO 文档 / Milvus 向量', '混合检索 / 规则与评测'],
    strengths: ['结构化数据、原始文件与向量检索分层', '把来源和规则校验带入问答过程']
  },
  BUILD: {
    id: 'BUILD',
    name: 'game-companion',
    tagline: '旁观式游戏与桌面陪伴',
    taglineEn: 'Bystander Game Co-pilot',
    desc: '开发中的桌面陪伴原型：跟随前台窗口与观察事件，通过可切换的 provider 生成建议，用树状 scope 隔离不同上下文的记忆。',
    tech: ['Python', 'PySide6', 'SQLite', '前台窗口跟随', 'Provider', '树状 scope 记忆'],
    status: 'wip',
    statusText: '持续开发中',
    url: `${BASE}/projects/game-companion`,
    urlLabel: 'GitHub', accent: '#a6c5fc',
    architecture: ['前台窗口跟随 / 观察事件', '上下文与树状 scope', 'SQLite 作用域记忆', '可切换 provider / 建议', 'PySide6 桌面陪伴与反馈'],
    strengths: ['按树状作用域组织记忆与上下文', '前台跟随与 provider 分离的陪伴架构']
  },
  DOCK: {
    id: 'DOCK',
    name: 'STARHARBOR',
    tagline: '群岛码头 · 起航点',
    taglineEn: 'Harbor Dock',
    desc: '把每个想法建成一座岛。从本地音乐处理、浏览器协作，到约束规划和知识检索，在可交互的作品中展示工程思考。联系：1483764005@qq.com',
    tech: ['Vue3', 'PixiJS', 'Blender', '本地进度'],
    status: 'live',
    statusText: '开放',
    url: `${BASE}/site`,
    urlLabel: '站点源码', accent: '#f0ca87',
    architecture: ['原创 Blender 场景', '透明等距渲染', 'PixiJS 社区与镜头', 'Vue 小游戏与真实产品录像', '静态 GitHub Pages'],
    strengths: ['可编辑的原创岛屿模型与轻量网页场景', '静态部署、键盘导航和独立音乐']
  }
}

export function isProjectId(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(PROJECTS, id)
}
