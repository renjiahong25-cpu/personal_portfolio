import { describe, expect, it } from 'vitest'
import { createDefaultStore } from './store'
import type { GroupRole, OrchestrationFlow } from './types'
import { buildAutoOrchestrationPrompt, normalizeAutoOrchestrationPlan, parseAutoOrchestrationPlan } from './orchestrationAutoPlan'

describe('orchestration auto plan', () => {
  it('normalizes model JSON with parallel fan-out and review fail branch', () => {
    const plan = parseAutoOrchestrationPlan(JSON.stringify({
      flowName: '文章流程',
      maxNodeExecutions: 30,
      roles: [
        { key: 'pm', reuseRoleId: 'role-pm', name: '产品经理', preferredSite: 'chatgpt' },
        { key: 'writer', name: '写手', description: '写正文', systemPrompt: '写清楚。', preferredSite: 'chatgpt' },
        { key: 'reviewer', name: '审核员', description: '审核质量', systemPrompt: '只判断 pass/fail。', preferredSite: 'deepseek' },
      ],
      nodes: [
        { id: 'plan', kind: 'execute', roleKeys: ['pm'], title: '规划', instruction: '拆解目标' },
        { id: 'write', kind: 'execute', roleKeys: ['writer'], title: '写作', instruction: '完成初稿' },
        { id: 'risk', kind: 'execute', roleKeys: ['reviewer'], title: '风险检查', instruction: '检查风险' },
        { id: 'review', kind: 'review', roleKeys: ['reviewer'], title: '审核', instruction: '判断是否通过', review: { criteria: '必须可交付', maxAttempts: 3, onMaxAttempts: 'stop' } },
      ],
      edges: [
        { from: 'plan', to: 'write' },
        { from: 'plan', to: 'risk' },
        { from: 'write', to: 'review' },
        { from: 'risk', to: 'review' },
        { from: 'review', to: 'write', branch: 'fail' },
      ],
    }), new Set(['role-pm']))

    expect(plan.roles.find(role => role.key === 'writer')?.preferredSite).toBe('chatgpt')
    expect(plan.edges).toEqual([
      { from: 'plan', to: 'write' },
      { from: 'plan', to: 'risk' },
      { from: 'write', to: 'review' },
      { from: 'risk', to: 'review' },
      { from: 'review', to: 'write', branch: 'fail' },
    ])
  })

  it('serializes same-role parallel nodes with an extra dependency edge', () => {
    const plan = normalizeAutoOrchestrationPlan({
      flowName: '串行化同人并行',
      roles: [{ key: 'writer', name: '写手', preferredSite: 'deepseek' }],
      nodes: [
        { id: 'start', kind: 'execute', roleKeys: ['writer'], title: '开始', instruction: '开始' },
        { id: 'a', kind: 'execute', roleKeys: ['writer'], title: 'A', instruction: 'A' },
        { id: 'b', kind: 'execute', roleKeys: ['writer'], title: 'B', instruction: 'B' },
      ],
      edges: [
        { from: 'start', to: 'a' },
        { from: 'start', to: 'b' },
      ],
    }, new Set())

    expect(plan.edges).toContainEqual({ from: 'a', to: 'b' })
  })

  it('normalizes display-name preferred sites from planner JSON', () => {
    const plan = normalizeAutoOrchestrationPlan({
      flowName: '站点名称归一',
      roles: [
        { key: 'chatgpt', name: 'ChatGPT 写手', preferredSite: 'ChatGPT' },
        { key: 'gemini', name: 'Gemini 分析师', preferredSite: 'Gemini' },
        { key: 'claude', name: 'Claude 审核员', preferredSite: 'Claude' },
        { key: 'deepseek', name: 'DeepSeek 助手', preferredSite: 'DeepSeek' },
        { key: 'grok', name: 'Grok 研究员', preferredSite: 'Grok' },
      ],
      nodes: [
        { id: 'n1', kind: 'execute', roleKeys: ['chatgpt'], title: '写作', instruction: '写作' },
        { id: 'n2', kind: 'execute', roleKeys: ['gemini'], title: '分析', instruction: '分析' },
        { id: 'n3', kind: 'execute', roleKeys: ['claude'], title: '审核', instruction: '审核' },
        { id: 'n4', kind: 'execute', roleKeys: ['deepseek'], title: '整理', instruction: '整理' },
        { id: 'n5', kind: 'execute', roleKeys: ['grok'], title: '研究', instruction: '研究' },
      ],
      edges: [],
    }, new Set())

    expect(plan.roles.map(role => role.preferredSite)).toEqual(['chatgpt', 'gemini', 'claude', 'deepseek', 'grok'])
  })

  it('accepts common planner site field aliases', () => {
    const plan = normalizeAutoOrchestrationPlan({
      flowName: '站点字段别名',
      roles: [
        { key: 'writer', name: '写手', site: 'ChatGPT' },
        { key: 'analyst', name: '分析师', chatSite: 'Gemini' },
        { key: 'reviewer', name: '审核员', preferred_site: 'Claude' },
      ],
      nodes: [
        { id: 'write', kind: 'execute', roleKeys: ['writer'], title: '写作', instruction: '写作' },
        { id: 'analysis', kind: 'execute', roleKeys: ['analyst'], title: '分析', instruction: '分析' },
        { id: 'review', kind: 'review', roleKeys: ['reviewer'], title: '审核', instruction: '审核', review: { criteria: '通过', maxAttempts: 1, onMaxAttempts: 'stop' } },
      ],
      edges: [],
    }, new Set())

    expect(plan.roles.map(role => role.preferredSite)).toEqual(['chatgpt', 'gemini', 'claude'])
  })

  it('rejects invalid role and edge references', () => {
    expect(() => normalizeAutoOrchestrationPlan({
      flowName: '坏流程',
      roles: [{ key: 'pm', reuseRoleId: 'missing', name: '产品经理' }],
      nodes: [{ id: 'n1', kind: 'execute', roleKeys: ['pm'], title: '执行', instruction: '执行' }],
      edges: [],
    }, new Set(['role-pm']))).toThrow('reuseRoleId 不存在')

    expect(() => normalizeAutoOrchestrationPlan({
      flowName: '坏流程',
      roles: [{ key: 'pm', name: '产品经理' }],
      nodes: [{ id: 'n1', kind: 'execute', roleKeys: ['pm'], title: '执行', instruction: '执行' }],
      edges: [{ from: 'n1', to: 'missing' }],
    }, new Set())).toThrow('连线引用了不存在的节点')
  })

  it('builds a prompt that tells the planner to use existing roles first and choose from supported chat sites', () => {
    const store = createDefaultStore()
    store.settings.language = 'zh-CN'
    const role: GroupRole = { id: 'role-1', chatId: 'chat-1', name: '产品经理', chatSite: 'chatgpt', description: '做产品', status: 'ready', contextCursor: 0, createdAt: 1, updatedAt: 1 }
    const prompt = buildAutoOrchestrationPrompt({ task: '做一个方案', existingRoles: [role], store })

    expect(prompt).toContain('优先复用 existingRoles')
    expect(prompt).toContain('preferredSite 必须使用 "chatgpt"、"gemini"、"claude"、"deepseek" 或 "grok"')
    expect(prompt).toContain('ChatGPT')
    expect(prompt).toContain('Gemini')
    expect(prompt).toContain('Claude')
    expect(prompt).toContain('DeepSeek')
    expect(prompt).toContain('Grok')
    expect(prompt).toContain('不要创建 kind=parallel')
    expect(prompt).toContain('"id": "role-1"')
  })

  it('builds an English planner prompt when English mode is selected', () => {
    const store = createDefaultStore()
    store.settings.language = 'en'
    const role: GroupRole = { id: 'role-1', chatId: 'chat-1', name: 'Product Manager', chatSite: 'chatgpt', description: 'Plans product work', status: 'ready', contextCursor: 0, createdAt: 1, updatedAt: 1 }
    const prompt = buildAutoOrchestrationPrompt({ task: 'Create a plan', existingRoles: [role], store })

    expect(prompt).toContain('Web Agent AI group-chat workflow planner')
    expect(prompt).toContain('Prefer reusing existingRoles')
    expect(prompt).toContain('Run task:')
    expect(prompt).toContain('Write generated role names, descriptions, systemPrompt, node titles, instructions, and review criteria in English')
    expect(prompt).not.toContain('优先复用 existingRoles')
  })

  it('builds a modification prompt with task, instruction, current flow and planner history separated', () => {
    const store = createDefaultStore()
    store.settings.language = 'zh-CN'
    const role: GroupRole = { id: 'role-1', chatId: 'chat-1', name: '写手', createdBy: 'orchestration-auto', chatSite: 'deepseek', systemPrompt: '写作人设', status: 'ready', contextCursor: 0, createdAt: 1, updatedAt: 1 }
    const currentFlow: OrchestrationFlow = {
      id: 'flow-1',
      chatId: 'chat-1',
      name: '文章流程',
      description: '写文章',
      stages: [{ id: 'stage-1', kind: 'roles', name: '写作', roleIds: ['role-1'], description: '写初稿' }],
      graph: { stageNodes: [{ id: 'stage-1', kind: 'roles', name: '写作', roleIds: ['role-1'], description: '写初稿' }], edges: [] },
      autoPlanHistory: [
        { id: 'auto-history-1', role: 'user', content: '先写后审', createdAt: 1 },
        { id: 'auto-history-2', role: 'assistant', content: '已生成 1 个节点', createdAt: 2 },
      ],
      maxRounds: 30,
      maxNodeExecutions: 30,
      createdAt: 1,
      updatedAt: 2,
    }

    const prompt = buildAutoOrchestrationPrompt({
      task: '写文章',
      instruction: '增加审核失败回写作',
      existingRoles: [role],
      currentFlow,
      history: currentFlow.autoPlanHistory,
      store,
    })

    expect(prompt).toContain('运行任务')
    expect(prompt).toContain('写文章')
    expect(prompt).toContain('本次编排指令')
    expect(prompt).toContain('增加审核失败回写作')
    expect(prompt).toContain('当前编排草稿')
    expect(prompt).toContain('"stage-1"')
    expect(prompt).toContain('自动编排历史')
    expect(prompt).toContain('先写后审')
    expect(prompt).toContain('写作人设')
  })
})
