export function keepDeepestResponseContainers(containers: Element[]): Element[] {
  return containers.filter(candidate => {
    return !containers.some(other => other !== candidate && candidate.contains(other))
  })
}

/**
 * Returns the number of response containers currently present on the page.
 * Used as a positional baseline that survives DOM rebuilds (unlike element references).
 */
export function countResponseContainers(containers: Element[]): number {
  return containers.length
}
