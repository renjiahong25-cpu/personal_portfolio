import assert from 'node:assert/strict'
import { uploadAndArrange, uploadAndTranscribe } from '../src/api.js'

const calls = []
globalThis.fetch = async (path, options) => {
  calls.push({ path, options })
  return { ok: true, text: async () => JSON.stringify({ upload: { key: 'test' } }) }
}
const file = new File([new Uint8Array([82, 73, 70, 70])], 'local-piano.wav', { type: 'audio/wav' })
for (const upload of [uploadAndArrange, uploadAndTranscribe]) {
  for (const analyse of [true, false]) {
    await upload(file, { analyse, transcriber: 'basic_pitch', melodySource: 'vocal_stem' })
    const { options } = calls.at(-1)
    assert.equal(options.body.get('audio').name, file.name)
    assert.deepEqual(new Uint8Array(await options.body.get('audio').arrayBuffer()), new Uint8Array(await file.arrayBuffer()))
    assert.equal(options.body.get('analyse'), analyse ? '1' : '0')
    assert.equal(options.body.get('transcriber'), 'basic_pitch')
    assert.equal(options.headers?.['Content-Type'], undefined)
  }
}
console.log('4 multipart upload cases passed: audio bytes, filename, mode, model and browser boundary')
