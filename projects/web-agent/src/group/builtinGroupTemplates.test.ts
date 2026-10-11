import { describe, expect, it } from 'vitest'
import {
  BUILTIN_GROUP_TEMPLATES,
  GROUP_TEMPLATE_ALL_CATEGORY,
  filterBuiltinGroupTemplates,
  getBuiltinGroupTemplate,
  getBuiltinGroupTemplateCategories,
} from './builtinGroupTemplates'

describe('built-in group templates', () => {
  it('offers ready-made groups across common industries and workflows', () => {
    expect(BUILTIN_GROUP_TEMPLATES).toHaveLength(45)
    expect(BUILTIN_GROUP_TEMPLATES.map(template => template.name)).toEqual(expect.arrayContaining([
      '学霸学习群',
      '论文写作群',
      'AI Agent 开发群',
      '合同审查群',
      '医疗问诊准备群',
      '金融研究群',
    ]))
  })

  it('keeps template categories in product order', () => {
    expect(getBuiltinGroupTemplateCategories()).toEqual([
      GROUP_TEMPLATE_ALL_CATEGORY,
      '学生与学习',
      '职场效率',
      '内容创作',
      '产品与创业',
      '市场营销与销售',
      '技术研发',
      '企业管理',
      '财务、法律、合规',
      '电商与本地生意',
      '专业服务',
      '行业垂直专家团',
    ])
  })

  it('searches names, categories, users, aliases and role prompts', () => {
    expect(filterBuiltinGroupTemplates({ category: GROUP_TEMPLATE_ALL_CATEGORY, query: '上岸' }).map(template => template.name)).toContain('考研 / 考公 / 考证作战群')
    expect(filterBuiltinGroupTemplates({ category: GROUP_TEMPLATE_ALL_CATEGORY, query: '写论文' }).map(template => template.name)).toContain('论文写作群')
    expect(filterBuiltinGroupTemplates({ category: GROUP_TEMPLATE_ALL_CATEGORY, query: '看合同有没有坑' }).map(template => template.name)[0]).toBe('合同审查群')
    expect(filterBuiltinGroupTemplates({ category: '技术研发', query: 'Agent' }).map(template => template.name)).toEqual(['AI Agent 开发群'])
  })

  it('defines complete reusable people for every group', () => {
    const ids = new Set<string>()

    for (const template of BUILTIN_GROUP_TEMPLATES) {
      expect(ids.has(template.id), template.id).toBe(false)
      ids.add(template.id)
      expect(template.defaultChatName.trim().length, template.id).toBeGreaterThan(0)
      expect(template.summary.trim().length, template.id).toBeGreaterThan(8)
      expect(template.userTypes.length, template.id).toBeGreaterThan(0)
      expect(template.aliases.length, template.id).toBeGreaterThan(0)
      expect(template.suggestedQuestions.length, template.id).toBeGreaterThanOrEqual(3)
      expect(template.roles.length, template.id).toBeGreaterThanOrEqual(3)
      expect(template.roles.length, template.id).toBeLessThanOrEqual(6)

      const roleNames = new Set<string>()
      for (const role of template.roles) {
        expect(roleNames.has(role.name), `${template.id}:${role.name}`).toBe(false)
        roleNames.add(role.name)
        expect(/\s/.test(role.name), `${template.id}:${role.name}`).toBe(false)
        expect(role.description.trim().length, `${template.id}:${role.name}`).toBeGreaterThan(8)
        expect(role.systemPrompt.trim().length, `${template.id}:${role.name}`).toBeGreaterThan(24)
      }
    }
  })

  it('can look up templates by stable id', () => {
    expect(getBuiltinGroupTemplate('thesis-writing')?.name).toBe('论文写作群')
    expect(getBuiltinGroupTemplate('missing-template')).toBeUndefined()
  })

  it('builds a template welcome prompt with startup questions and risk boundaries', async () => {
    const { buildBuiltinGroupTemplateWelcomeMessage } = await import('./builtinGroupTemplates')
    const contractTemplate = getBuiltinGroupTemplate('contract-review')!

    const welcome = buildBuiltinGroupTemplateWelcomeMessage(contractTemplate)

    expect(welcome).toContain('欢迎来到「合同审查群」')
    expect(welcome).toContain('你的目标、当前情况、限制条件、期望输出')
    expect(welcome).toContain('我想解决什么问题？')
    expect(welcome).toContain('帮我看合同里有没有坑')
    expect(welcome).toContain('仅用于信息整理和风险提示')
  })
})
