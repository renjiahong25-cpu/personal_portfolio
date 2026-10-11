import { describe, expect, it } from 'vitest'
import { buildOrchestrationReviewMessageContent, buildOrchestrationRoleMessageContent } from './orchestrationPrompts'
import type { OrchestrationReviewResult, OrchestrationStage } from './types'

describe('orchestration message content builders', () => {
  it('builds a role-node group message without duplicating persona or group context logic', () => {
    const content = buildOrchestrationRoleMessageContent({
      userTask: 'Create a launch plan.',
      currentStage: makeRoleStage(),
      previousReviewResult: makeReviewResult(),
      language: 'zh-CN',
    })

    expect(content).toContain('当前任务：\nCreate a launch plan.')
    expect(content).toContain('当前节点任务：\nTurn the research into a launch-ready draft.')
    expect(content).toContain('上次审核未通过要求：\nAddress launch risks.')
    expect(content).not.toContain('群聊成员')
    expect(content).not.toContain('你上次之后')
    expect(content).not.toContain('Role persona')
  })

  it('builds a review-node group message that strongly requires JSON only', () => {
    const content = buildOrchestrationReviewMessageContent({
      userTask: 'Create a launch plan.',
      currentStage: makeReviewStage(),
      reviewCriteria: 'Plan must include risks and owner.',
      language: 'zh-CN',
    })

    expect(content).toContain('审核标准：\nPlan must include risks and owner.')
    expect(content).toContain('你必须只返回合法 JSON')
    expect(content).toContain('"decision": "pass | fail"')
    expect(content).toContain('"nextRoundInstruction": "decision 为 fail 时必填，否则为空字符串"')
    expect(content).not.toContain('群聊成员')
    expect(content).not.toContain('你上次之后')
  })

  it('builds English orchestration instructions when English mode is selected', () => {
    const content = buildOrchestrationReviewMessageContent({
      userTask: 'Create a launch plan.',
      currentStage: makeReviewStage(),
      reviewCriteria: 'Plan must include risks and owner.',
      language: 'en',
    })

    expect(content).toContain('Current task:\nCreate a launch plan.')
    expect(content).toContain('Review criteria:\nPlan must include risks and owner.')
    expect(content).toContain('Return valid JSON only')
    expect(content).toContain('"reason": "Review explanation"')
    expect(content).not.toContain('审核标准')
  })
})

function makeRoleStage(): OrchestrationStage {
  return {
    id: 'stage-draft',
    kind: 'roles',
    name: 'Draft',
    roleIds: ['role-writer'],
    description: 'Turn the research into a launch-ready draft.',
  }
}

function makeReviewStage(): OrchestrationStage {
  return {
    id: 'stage-review',
    kind: 'review',
    name: 'Review',
    roleIds: [],
    review: { reviewerRoleIds: ['role-reviewer'], instructions: 'Check completeness.' },
  }
}

function makeReviewResult(): OrchestrationReviewResult {
  return {
    round: 1,
    stageRunId: 'stage-run-1',
    reviewerRoleId: 'role-reviewer',
    messageId: 'msg-review',
    decision: 'fail',
    reason: 'Missing launch risks.',
    failedCriteria: ['risks'],
    nextRoundInstruction: 'Address launch risks.',
    rawJson: '{"decision":"fail"}',
    createdAt: 1,
  }
}
