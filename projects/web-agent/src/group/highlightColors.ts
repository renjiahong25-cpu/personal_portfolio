export const MESSAGE_HIGHLIGHT_COLORS = [
  { value: '#f8b84e', label: '琥珀', rgb: '248, 184, 78' },
  { value: '#7dd3fc', label: '蓝色', rgb: '125, 211, 252' },
  { value: '#86efac', label: '绿色', rgb: '134, 239, 172' },
  { value: '#f9a8d4', label: '粉色', rgb: '249, 168, 212' },
  { value: '#c4b5fd', label: '紫色', rgb: '196, 181, 253' },
] as const

export type MessageHighlightColor = typeof MESSAGE_HIGHLIGHT_COLORS[number]['value']

export const DEFAULT_MESSAGE_HIGHLIGHT_COLOR: MessageHighlightColor = '#f8b84e'

export function normalizeMessageHighlightColor(value: unknown): MessageHighlightColor {
  return isMessageHighlightColor(value) ? value : DEFAULT_MESSAGE_HIGHLIGHT_COLOR
}

export function isMessageHighlightColor(value: unknown): value is MessageHighlightColor {
  return typeof value === 'string' && MESSAGE_HIGHLIGHT_COLORS.some(color => color.value === value)
}

export function messageHighlightColorRgb(value: unknown): string {
  return MESSAGE_HIGHLIGHT_COLORS.find(color => color.value === normalizeMessageHighlightColor(value))?.rgb ?? MESSAGE_HIGHLIGHT_COLORS[0].rgb
}
