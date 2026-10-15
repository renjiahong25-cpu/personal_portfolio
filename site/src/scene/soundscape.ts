import { reactive } from 'vue'
import { MUSIC_ISLAND_IDS, parseMusicTrack, type MusicTrack } from '../data/music'
const tracks = new Map<string, MusicTrack>()
export const audioState = reactive({ enabled: false, playing: false, ready: false, officialUrl: null as string|null, officialSongUrl: null as string|null, selectedTitle: '', message: '默认静音 · 点击开启音乐' })
let audio: HTMLAudioElement | null = null, selected = 'HOME', volume = .32, suppressed = false, generation = 0, disposed = false
const controller = new AbortController()
function ensureAudio(): HTMLAudioElement {
  if (!audio) {
    audio = new Audio(); audio.loop = true; audio.preload = 'metadata'; audio.volume = volume
    audio.addEventListener('error', () => { if(tracks.get(selected)?.provider!=='file')return;audioState.playing = false; audioState.message = '这首音乐暂时无法播放，请检查音频文件。' })
    audio.addEventListener('pause', () => { audioState.playing = false })
    audio.addEventListener('playing', () => { const track=tracks.get(selected);if(track?.provider!=='file'||audio?.getAttribute('src')!==track.src||suppressed||document.hidden||!audioState.enabled){audio?.pause();return};audioState.playing = true; audioState.message = '' })
  }
  return audio
}
async function playSelection(): Promise<boolean> {
  const token = ++generation, track = tracks.get(selected)
  audio?.pause();audioState.officialUrl=null;audioState.officialSongUrl=null;audioState.playing=false
  if (!track) { audio?.removeAttribute('src'); audio?.load(); audioState.message = '此岛音乐待接入'; return false }
  audioState.selectedTitle=`${track.artist} / ${track.title}`
  if (!audioState.enabled || suppressed || document.hidden || disposed) return false
  if(track.provider==='netease'){
    audio?.removeAttribute('src');audio?.load()
    audioState.officialUrl=track.playerUrl;audioState.officialSongUrl=track.songUrl
    audioState.message='网易云官方外部播放器已打开；请点按其中的播放键。'
    return true
  }
  const player=ensureAudio()
  if (player.getAttribute('src') !== track.src) { player.src = track.src; player.load() }
  try { await player.play(); if (token !== generation || suppressed || document.hidden || disposed || !audioState.enabled) { if(token===generation)player.pause(); return false }; return true }
  catch { if(token === generation){audioState.playing=false;audioState.message='点按音乐按钮开始播放。'}; return false }
}
export const soundscape = {
  async init(): Promise<void> {
    try {
      const response = await fetch(import.meta.env.BASE_URL + 'audio/manifest.json', { signal: controller.signal }); if (!response.ok) return
      const data: unknown = await response.json()
      if (!data || typeof data !== 'object' || (data as {version?:unknown}).version !== 1 || !Array.isArray((data as {tracks?:unknown}).tracks)) return
      for (const item of ((data as {tracks:unknown[]}).tracks).slice(0, 7)) {
        const track=parseMusicTrack(item)
        if(!track||tracks.has(track.island))continue
        tracks.set(track.island,track)
      }
      if (!disposed) audioState.ready = tracks.size > 0
    } catch { if (!controller.signal.aborted) audioState.message = '授权音乐素材尚未接入' }
  },
  title(id: string): string { const t=tracks.get(id); return t ? `${t.artist} / ${t.title}` : '黄霄云歌单 · 授权音源待接入' },
  async toggle(): Promise<boolean> {
    if(disposed)return false
    if(audioState.enabled){audioState.enabled=false;generation++;audio?.pause();audioState.officialUrl=null;audioState.officialSongUrl=null;audioState.message='音乐已关闭';return false}
    if(!tracks.has(selected)){audioState.message='此岛音乐尚未接入';return false}
    audioState.enabled=true
    const playing=await playSelection();if(!playing&&!suppressed)audioState.enabled=false
    return audioState.enabled
  },
  select(id: string): void { if (!MUSIC_ISLAND_IDS.some(x=>x===id) || selected===id) return; selected=id;generation++;audio?.pause();if(audioState.enabled)void playSelection() },
  blind(_index:number):void {soundscape.select('HOME')},
  setVolume(value:number):void {if(!Number.isFinite(value))return;volume=Math.min(.7,Math.max(0,value));if(audio)audio.volume=volume},
  setPaused(value:boolean):void {if(suppressed===value)return;suppressed=value;generation++;audio?.pause();audioState.officialUrl=null;audioState.officialSongUrl=null;if(!value&&audioState.enabled)void playSelection()},
  dispose():void {disposed=true;controller.abort();generation++;audioState.enabled=false;audioState.officialUrl=null;audioState.officialSongUrl=null;audio?.pause();audio?.removeAttribute('src');audio?.load();audio=null;tracks.clear();audioState.ready=false}
}
