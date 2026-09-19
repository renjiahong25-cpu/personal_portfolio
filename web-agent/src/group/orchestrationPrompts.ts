import { PROMPT_I18N, normalizeLanguage } from '../shared/i18n'
import type { WebAgentLanguage, OrchestrationReviewResult, OrchestrationStage } from './types'

export interface BuildOrchestrationRoleMessageContentInput {
  userTask: string
  currentStage: OrchestrationStage
  previousReviewResult?: OrchestrationReviewResult
  language?: WebAgentLanguage
}

export interface BuildOrchestrationReviewMessageContentInput {
  userTask: string
  currentStage: OrchestrationStage
  reviewCriteria?: string
  language?: WebAgentLanguage
}

export function buildOrchestrationRoleMessageContent(input: BuildOrchestrationRoleMessageContentInput): string {
  const language = normalizeLanguage(input.language)
  const previousInstruction = input.previousReviewResult?.nextRoundInstruction?.trim()
  return joinSections([
    `${language === 'en' ? 'Current task:' : '当前任务：'}\n${input.userTask}`,
    input.currentStage.description ? `${language === 'en' ? 'Current node task:' : '当前节点任务：'}\n${input.currentStage.description}` : undefined,
    previousInstruction ? `${language === 'en' ? 'Previous review failed with this requirement:' : '上次审核未通过要求：'}\n${previousInstruction}` : undefined,
  ])
}

export function buildOrchestrationReviewMessageContent(input: BuildOrchestrationReviewMessageContentInput): string {
  const language = normalizeLanguage(input.language)
  return joinSections([
    `${language === 'en' ? 'Current task:' : '当前任务：'}\n${input.userTask}`,
    input.currentStage.description ? `${language === 'en' ? 'Current review node task:' : '当前审核节点任务：'}\n${input.currentStage.description}` : undefined,
    `${language === 'en' ? 'Review criteria:' : '审核标准：'}\n${input.reviewCriteria?.trim() || input.currentStage.review?.instructions?.trim() || (language === 'en' ? 'Decide whether the current result satisfies the task requirements.' : '判断当前结果是否满足任务要求。')}`,
    buildOrchestrationReviewResponseInstruction(language),
  ])
}

export function buildOrchestrationReviewResponseInstruction(languageInput?: WebAgentLanguage): string {
  const language = normalizeLanguage(languageInput)
  return joinSections([
    PROMPT_I18N[language].jsonOnly,
    `${language === 'en' ? 'The JSON format must match:' : 'JSON 格式必须匹配：'}\n${reviewSchema(language)}`,
    language === 'en'
      ? 'decision must be pass or fail. Use fail when the review does not pass, and write exactly what to improve in nextRoundInstruction.'
      : 'decision 只能是 pass 或 fail。不通过时使用 fail，并在 nextRoundInstruction 写清楚下一次重试要改什么。',
  ])
}

function reviewSchema(language: WebAgentLanguage): string {
  const schema = language === 'en'
    ? {
        decision: 'pass | fail',
        reason: 'Review explanation',
        failedCriteria: ['Criteria that were not met'],
        nextRoundInstruction: 'Required when decision is fail; otherwise an empty string',
      }
    : {
        decision: 'pass | fail',
        reason: '审核说明',
        failedCriteria: ['未满足的标准'],
        nextRoundInstruction: 'decision 为 fail 时必填，否则为空字符串',
      }
  return JSON.stringify(schema, null, 2)
}

function joinSections(sections: Array<string | undefined>): string {
  return sections.filter((section): section is string => Boolean(section?.trim())).join('\n\n')
}
