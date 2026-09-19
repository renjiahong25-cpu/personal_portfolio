import type { ReviewDecision } from './types'

export interface ParsedReviewDecision {
  decision: ReviewDecision
  reason: string
  failedCriteria: string[]
  nextRoundInstruction: string
  rawJson: string
}

export type ParseReviewDecisionResult =
  | { ok: true; decision: ParsedReviewDecision }
  | { ok: false; error: string }

export function parseReviewDecision(raw: string): ParseReviewDecisionResult {
  const rawJson = extractJson(raw)
  if (!rawJson) {
    return { ok: false, error: 'Review response did not contain JSON.' }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(rawJson)
  } catch {
    return { ok: false, error: 'Review response JSON is invalid.' }
  }

  if (!isRecord(parsed)) {
    return { ok: false, error: 'Review response JSON must be an object.' }
  }

  const decision = parsed.decision
  if (decision !== 'pass' && decision !== 'fail') {
    return { ok: false, error: 'Review decision must be pass or fail.' }
  }

  if (typeof parsed.reason !== 'string' || parsed.reason.trim().length === 0) {
    return { ok: false, error: 'Review reason must be a non-empty string.' }
  }

  if (!Array.isArray(parsed.failedCriteria) || !parsed.failedCriteria.every(item => typeof item === 'string')) {
    return { ok: false, error: 'Review failedCriteria must be a string array.' }
  }

  if (typeof parsed.nextRoundInstruction !== 'string') {
    return { ok: false, error: 'Review nextRoundInstruction must be a string.' }
  }

  if (decision === 'fail' && parsed.nextRoundInstruction.trim().length === 0) {
    return { ok: false, error: 'Review nextRoundInstruction is required when decision is fail.' }
  }

  return {
    ok: true,
    decision: {
      decision,
      reason: parsed.reason.trim(),
      failedCriteria: parsed.failedCriteria,
      nextRoundInstruction: parsed.nextRoundInstruction.trim(),
      rawJson,
    },
  }
}

function extractJson(raw: string): string | undefined {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)\s*```/i)
  const candidate = (fenced?.[1] ?? raw).trim()
  return candidate.length > 0 ? candidate : undefined
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
