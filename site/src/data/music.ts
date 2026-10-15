import { ISLAND_IDS, type IslandId } from './community'
import { assetPath } from './media'
export const MUSIC_ISLAND_IDS = ['HOME', ...ISLAND_IDS] as const
export type MusicIslandId = IslandId | 'HOME'
interface TrackInfo { island: MusicIslandId; title: string; artist: string }
export type MusicTrack = TrackInfo & ({ provider: 'file'; src: string } | { provider: 'netease'; songId: string; playerUrl: string; songUrl: string })
export function parseMusicTrack(value: unknown): MusicTrack | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const x=value as Record<string,unknown>
  if(typeof x.island!=='string'||!MUSIC_ISLAND_IDS.some(id=>id===x.island)||typeof x.title!=='string'||!x.title.trim()||x.title.length>160||typeof x.artist!=='string'||!x.artist.trim()||x.artist.length>100)return null
  const info:TrackInfo={island:x.island as MusicIslandId,title:x.title,artist:x.artist}
  if(x.provider==='netease'){
    if(typeof x.songId!=='string'||!/^[1-9][0-9]{0,19}$/.test(x.songId)||Object.hasOwn(x,'src')||Object.hasOwn(x,'authorized'))return null
    return {...info,provider:'netease',songId:x.songId,playerUrl:`https://music.163.com/outchain/player?type=2&id=${x.songId}&auto=0&height=66`,songUrl:`https://music.163.com/#/song?id=${x.songId}`}
  }
  if(x.provider!==undefined&&x.provider!=='file')return null
  const src=assetPath(x.src,'audio')
  if(x.authorized!==true||Object.hasOwn(x,'songId')||!src||!/\.(mp3|m4a|ogg|wav)$/i.test(src))return null
  return {...info,provider:'file',src}
}
