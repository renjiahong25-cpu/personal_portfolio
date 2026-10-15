import { isProjectId } from './projects'
export interface ProjectVideo { id: string; project: string; title: string; description: string; src: string; poster?: string; duration?: string }
export function assetPath(value: unknown, folder: string): string | null {
  if (typeof value !== 'string' || value.length > 240 || !new RegExp(`^${folder}/[a-zA-Z0-9_./-]+$`).test(value) || value.includes('..') || value.includes('//')) return null
  return import.meta.env.BASE_URL + value
}
export async function loadVideos(signal: AbortSignal): Promise<ProjectVideo[]> {
  const response = await fetch(import.meta.env.BASE_URL + 'media/manifest.json', { signal })
  if (!response.ok) throw new Error('录屏素材尚未接入。')
  const data: unknown = await response.json()
  if (!data || typeof data !== 'object' || (data as {version?:unknown}).version !== 1 || !Array.isArray((data as {videos?:unknown}).videos)) throw new Error('录屏清单格式无效。')
  const ids = new Set<string>()
  return ((data as {videos: unknown[]}).videos).slice(0, 30).flatMap(v => {
    if (!v || typeof v !== 'object') return []
    const x = v as Record<string, unknown>
    if (typeof x.id !== 'string' || !/^[\w-]{1,60}$/.test(x.id) || ids.has(x.id) || typeof x.project !== 'string' || !isProjectId(x.project) || typeof x.title !== 'string' || x.title.length > 160 || typeof x.description !== 'string' || x.description.length > 600 || !assetPath(x.src, 'media') || !/\.(mp4|webm)$/i.test(x.src as string)) return []
    ids.add(x.id)
    return [{ id: x.id, project: x.project, title: x.title, description: x.description, src: assetPath(x.src, 'media')!, poster: assetPath(x.poster, 'media') ?? undefined, duration: typeof x.duration === 'string' ? x.duration.slice(0, 12) : undefined }]
  })
}
