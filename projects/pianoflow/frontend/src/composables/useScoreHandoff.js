/**
 * Handoff: a downloaded wav is ready to be transcribed / arranged. The source
 * page sets the key and navigates; the destination panel picks it up on mount.
 */
import { ref } from 'vue'

export const pendingCacheKey = ref('')

export function handoffToScore(key) {
  pendingCacheKey.value = key
}

export function takePendingKey() {
  const key = pendingCacheKey.value
  pendingCacheKey.value = ''
  return key
}
