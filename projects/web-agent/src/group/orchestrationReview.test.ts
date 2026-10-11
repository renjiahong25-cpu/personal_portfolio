import { describe, expect, it } from 'vitest'
import { parseReviewDecision } from './orchestrationReview'

describe('parseReviewDecision', () => {
  it('parses plain JSON decisions', () => {
    const result = parseReviewDecision(JSON.stringify({
      decision: 'pass',
      reason: 'Meets the acceptance criteria.',
      failedCriteria: [],
      nextRoundInstruction: '',
    }))

    expect(result).toEqual({
      ok: true,
      decision: {
        decision: 'pass',
        reason: 'Meets the acceptance criteria.',
        failedCriteria: [],
        nextRoundInstruction: '',
        rawJson: '{"decision":"pass","reason":"Meets the acceptance criteria.","failedCriteria":[],"nextRoundInstruction":""}',
      },
    })
  })

  it('parses fenced JSON decisions', () => {
    const result = parseReviewDecision('```json\n{"decision":"fail","reason":"Missing safety handling.","failedCriteria":["safety"],"nextRoundInstruction":"补充安全处理。"}\n```')

    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.decision.decision).toBe('fail')
      expect(result.decision.failedCriteria).toEqual(['safety'])
    }
  })

  it('rejects invalid JSON instead of returning a flow decision', () => {
    const result = parseReviewDecision('{"decision":"pass"')

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error).toContain('invalid')
    }
  })

  it('validates required fields and enum values', () => {
    expect(parseReviewDecision('{"decision":"retry","reason":"Try again","failedCriteria":[],"nextRoundInstruction":"More detail"}').ok).toBe(false)
    expect(parseReviewDecision('{"decision":"pass","reason":"","failedCriteria":[],"nextRoundInstruction":""}').ok).toBe(false)
    expect(parseReviewDecision('{"decision":"pass","reason":"OK","failedCriteria":"none","nextRoundInstruction":""}').ok).toBe(false)
    expect(parseReviewDecision('{"decision":"pass","reason":"OK","failedCriteria":[]}').ok).toBe(false)
  })

  it('requires nextRoundInstruction for fail decisions', () => {
    const invalid = parseReviewDecision('{"decision":"fail","reason":"Missing details","failedCriteria":["coverage"],"nextRoundInstruction":""}')
    const valid = parseReviewDecision('{"decision":"fail","reason":"Missing details","failedCriteria":["coverage"],"nextRoundInstruction":"Add implementation risks."}')

    expect(invalid.ok).toBe(false)
    expect(valid.ok).toBe(true)
    if (valid.ok) {
      expect(valid.decision.nextRoundInstruction).toBe('Add implementation risks.')
    }
  })
})
