export function isEmbeddedFrame(): boolean {
  try {
    return window.top !== window
  } catch {
    return true
  }
}

export function isDirectEmbeddedFrame(): boolean {
  try {
    return window.top !== window && window.parent === window.top
  } catch {
    return false
  }
}
