import { reactive } from 'vue'
export const ISLAND_IDS = ['DOCK', 'PIANO', 'HUB', 'JOURNEY', 'TARIFF', 'BUILD'] as const
export type IslandId = typeof ISLAND_IDS[number]
export interface IslandProgress { rounds: number; best: number; supplies: number }
export interface Progress { version: 1; islands: Record<IslandId, IslandProgress> }
export const SAVE_KEY = 'starharbor.community.v1'
const record = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x)
const exact = (x: Record<string, unknown>, keys: readonly string[]) => Object.keys(x).length === keys.length && keys.every(k => Object.hasOwn(x, k))
export function validProgress(x: unknown): x is Progress {
  if (!record(x) || !exact(x, ['version', 'islands']) || x.version !== 1 || !record(x.islands) || !exact(x.islands, ISLAND_IDS)) return false
  return ISLAND_IDS.every(id => {
    const p = x.islands as Record<string, unknown>, v = p[id]
    return record(v) && exact(v, ['rounds', 'best', 'supplies']) && ['rounds', 'best', 'supplies'].every(k => Number.isSafeInteger(v[k]) && (v[k] as number) >= 0 && (v[k] as number) <= 1000000)
  })
}
export function freshProgress(): Progress {
  return { version: 1, islands: Object.fromEntries(ISLAND_IDS.map(id => [id, { rounds: 0, best: 0, supplies: 0 }])) as Progress['islands'] }
}
export function loadProgress(): Progress {
  try { const raw = localStorage.getItem(SAVE_KEY); if (!raw || raw.length > 4096) return freshProgress(); const value: unknown = JSON.parse(raw); return validProgress(value) ? value : freshProgress() } catch { return freshProgress() }
}
export const progress = reactive(loadProgress())
export const storageStatus = reactive({ saved: true })
export function completeRound(id: IslandId, score: number): void {
  if (!ISLAND_IDS.includes(id) || !Number.isSafeInteger(score) || score < 0 || score > 1000) return
  const p = progress.islands[id]
  p.rounds = Math.min(1000000, p.rounds + 1); p.best = Math.max(p.best, score); p.supplies = Math.min(1000000, p.supplies + Math.floor(score / 10))
  try { localStorage.setItem(SAVE_KEY, JSON.stringify(progress)); storageStatus.saved = true } catch { storageStatus.saved = false }
}
export const COMMUNITY: Record<IslandId, { title: string; game: string; role: string; action: string; residents: string[] }> = {
  DOCK: { title: '潮汐农园', game: '种植', role: '园丁', action: '照料花田，向群岛送出新鲜补给', residents: ['阿芽', '小禾', '灯灯'] },
  PIANO: { title: '共鸣练习室', game: '节奏', role: '乐手', action: '排练夜间音乐会，收集准确的节拍', residents: ['音音', '小拍', '弦弦'] },
  HUB: { title: '信号邮局', game: '排序', role: '调度员', action: '分工、执行、复核，让协作有序抵达', residents: ['小信', '点点', '阿序'] },
  JOURNEY: { title: '林间探险队', game: '探索', role: '向导', action: '沿步道找到露营补给，规划一场相聚', residents: ['森森', '小岚', '阿途'] },
  TARIFF: { title: '铜色货运港', game: '运输', role: '船长', action: '避开礁石，把货物送到正确的码头', residents: ['小帆', '阿港', '铜铜'] },
  BUILD: { title: '星灯工坊', game: '建造', role: '建造师', action: '铺设连通步道，让居民有自己的家', residents: ['阿筑', '小星', '方方'] }
}
