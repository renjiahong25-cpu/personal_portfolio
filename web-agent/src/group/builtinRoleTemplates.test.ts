import { describe, expect, it } from 'vitest'
import { BUILTIN_GROUP_TEMPLATES } from './builtinGroupTemplates'
import { BUILTIN_ROLE_TEMPLATES } from './builtinRoleTemplates'
import type { RoleTemplate } from './types'

type TemplateWithSource = RoleTemplate & {
  category?: string
  sourceTemplateId?: string
  sourceTemplateName?: string
}

describe('built-in role templates', () => {
  it('includes every built-in group member as a categorized scenario person', () => {
    const scenarioTemplates = BUILTIN_ROLE_TEMPLATES.filter((template): template is TemplateWithSource => Boolean((template as TemplateWithSource).sourceTemplateId))
    const groupMemberCount = BUILTIN_GROUP_TEMPLATES.reduce((total, template) => total + template.roles.length, 0)

    expect(scenarioTemplates).toHaveLength(groupMemberCount)
    expect(scenarioTemplates.every(template => template.type === 'builtin')).toBe(true)
    expect(new Set(scenarioTemplates.map(template => template.defaultChatSite))).toEqual(new Set(['deepseek']))
    expect(scenarioTemplates.every(template => template.category && template.sourceTemplateName)).toBe(true)
    expect(scenarioTemplates.map(template => template.name)).toEqual(expect.arrayContaining([
      '学习规划师',
      'Prompt规范工程师',
      '短视频·标题封面顾问',
      '小红书·标题封面顾问',
      '餐饮·成本控制师',
      '制造业·成本控制师',
    ]))
  })

  it('keeps scenario people uniquely identifiable and searchable by source metadata', () => {
    const ids = new Set(BUILTIN_ROLE_TEMPLATES.map(template => template.id))
    const agentPromptEngineer = BUILTIN_ROLE_TEMPLATES.find((template): template is TemplateWithSource => template.name === 'Prompt规范工程师')
    const restaurantCostController = BUILTIN_ROLE_TEMPLATES.find((template): template is TemplateWithSource => template.name === '餐饮·成本控制师')
    const manufacturingCostController = BUILTIN_ROLE_TEMPLATES.find((template): template is TemplateWithSource => template.name === '制造业·成本控制师')

    expect(ids.size).toBe(BUILTIN_ROLE_TEMPLATES.length)
    expect(agentPromptEngineer).toMatchObject({
      category: '技术研发',
      sourceTemplateName: 'AI Agent 开发群',
      systemPrompt: expect.stringContaining('Prompt'),
    })
    expect(restaurantCostController).toMatchObject({
      category: '电商与本地生意',
      sourceTemplateName: '餐饮经营群',
    })
    expect(manufacturingCostController).toMatchObject({
      category: '行业垂直专家团',
      sourceTemplateName: '制造业改善群',
    })
  })
})
