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
}

const BASE = 'https://github.com/renjiahong25-cpu/personal_portfolio/tree/main'

export const PROJECTS: Record<string, ProjectInfo> = {
  HUB: {
    id: 'HUB',
    name: 'web-agent',
    tagline: '多AI网页会话协作',
    taglineEn: 'Multi-AI Web Session Collaboration',
    desc: '多智能体网页协作工作台：多个 AI 与人类在同一浏览器会话中实时共享上下文、分工编排任务、全程留痕可审计。',
    tech: ['Node.js', 'TypeScript', 'Multi-Agent', '浏览器自动化'],
    status: 'live',
    statusText: '运行中',
    url: `${BASE}/projects/web-agent`,
    urlLabel: 'GitHub'
  },
  PIANO: {
    id: 'PIANO',
    name: 'PianoFlow',
    tagline: '本地AI音乐工作台',
    taglineEn: 'Local AI Music Workbench',
    desc: '从音频到 MIDI 的全本地音乐工作台：basic-pitch（ONNX）音高检测、审美映射规则、OSMD 乐谱渲染。核心链路已完成，播放器开发中。',
    tech: ['Python', 'Tornado', 'basic-pitch · ONNX', 'Vue3', 'OSMD'],
    status: 'progress',
    statusText: '核心完成 · 播放器开发中',
    url: `${BASE}/projects/pianoflow`,
    urlLabel: 'GitHub'
  },
  JOURNEY: {
    id: 'JOURNEY',
    name: 'venue-scout',
    tagline: '团建场地智能规划',
    taglineEn: 'Team-Building Venue Scout',
    desc: '团建 / 拓展 / 年会场地采集 + 攻略级行程规划：多源信息聚合、LLM 推理筛选、生成可落地的行程与 Excel 汇总报告。',
    tech: ['Python', 'LLM', '信息聚合', 'Excel 报告'],
    status: 'live',
    statusText: '运行中',
    url: `${BASE}/projects/venue-scout`,
    urlLabel: 'GitHub'
  },
  TARIFF: {
    id: 'TARIFF',
    name: 'supplychainpolicyagent',
    tagline: '跨境关税与政策智能体',
    taglineEn: 'Cross-border Tariff & Policy Agent',
    desc: '跨境贸易政策智能体：政策采集与结构化、混合检索知识库、关税与合规问答、带评测回归的规则层。',
    tech: ['Vue3', 'Node.js', '混合检索', '政策知识库'],
    status: 'live',
    statusText: '运行中',
    url: `${BASE}/projects/supplychainpolicyagent`,
    urlLabel: 'GitHub'
  },
  BUILD: {
    id: 'BUILD',
    name: 'game-companion',
    tagline: '游戏副驾驶（开发中）',
    taglineEn: 'Bystander Game Co-pilot',
    desc: '长期项目：懂你所玩游戏的游戏副驾驶。旁观式陪伴、不注入任何输入、从你的反馈中学习偏好、跨游戏提供建议。clean-room 开发，MIT 协议。',
    tech: ['Python', 'PySide6', 'SQLite', '信号总线'],
    status: 'wip',
    statusText: '施工中 0%',
    url: null,
    urlLabel: ''
  },
  DOCK: {
    id: 'DOCK',
    name: 'STARHARBOR',
    tagline: '群岛码头 · 起航点',
    taglineEn: 'Harbor Dock',
    desc: 'rjh-ghost 的作品群岛：把每个想法建成一座岛。联系方式与起航点。',
    tech: ['GitHub'],
    status: 'live',
    statusText: '开放',
    url: 'https://github.com/renjiahong25-cpu',
    urlLabel: 'GitHub 主页'
  }
}
