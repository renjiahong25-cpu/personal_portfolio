import { afterEach, describe, expect, it } from 'vitest'
import { connect } from 'node:net'
import { DEFAULT_PORT, createControlDaemon, extractImageUrls, getImageExtension } from './web-agent-daemon.mjs'

describe('web-agent local daemon', () => {
  const daemons = []

  afterEach(async () => {
    await Promise.all(daemons.map(daemon => daemon.close()))
    daemons.length = 0
  })

  it('uses the shared Web Agent control default port', () => {
    expect(DEFAULT_PORT).toBe(19305)
  })

  it('forwards authenticated HTTP commands to the connected extension WebSocket', async () => {
    const daemon = await createControlDaemon({ port: 0, token: 'test-token', logToConsole: false })
    daemons.push(daemon)
    const extension = new WebSocket(`ws://127.0.0.1:${daemon.port}/ext?profileId=test-profile`)
    const commands = []
    extension.addEventListener('open', () => {
      extension.send(JSON.stringify({
        type: 'hello',
        extensionVersion: '1.0.0',
        protocolVersion: 1,
        profileId: 'test-profile',
        capabilities: ['chat.list'],
      }))
    })
    extension.addEventListener('message', event => {
      const message = JSON.parse(String(event.data))
      if (message.type !== 'command') return
      commands.push(message.command)
      extension.send(JSON.stringify({
        type: 'result',
        result: {
          id: message.command.id,
          ok: true,
          data: { chats: [{ id: 'chat-1', name: '评审群' }] },
        },
      }))
    })
    await waitFor(() => daemon.status().extensionConnected)

    const response = await fetch(`http://127.0.0.1:${daemon.port}/command`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Web-Agent': '1',
        Authorization: 'Bearer test-token',
      },
      body: JSON.stringify({ id: 'cmd-1', action: 'chat.list' }),
    })

    await expect(response.json()).resolves.toEqual({
      id: 'cmd-1',
      ok: true,
      data: { chats: [{ id: 'chat-1', name: '评审群' }] },
    })
    expect(commands).toEqual([{ id: 'cmd-1', action: 'chat.list' }])
    extension.close()
  })

  it('rejects commands when the bearer token is missing', async () => {
    const daemon = await createControlDaemon({ port: 0, token: 'test-token', logToConsole: false })
    daemons.push(daemon)

    const response = await fetch(`http://127.0.0.1:${daemon.port}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Web-Agent': '1' },
      body: JSON.stringify({ id: 'cmd-1', action: 'chat.list' }),
    })

    expect(response.status).toBe(401)
    await expect(response.json()).resolves.toMatchObject({
      ok: false,
      error: { code: 'permission_denied' },
    })
  })

  it('accepts fragmented extension WebSocket messages', async () => {
    const daemon = await createControlDaemon({ port: 0, token: 'test-token', logToConsole: false })
    daemons.push(daemon)
    const socket = await openRawWebSocket(daemon.port, '/ext?profileId=test-profile')
    const hello = JSON.stringify({
      type: 'hello',
      extensionVersion: '1.0.0',
      protocolVersion: 1,
      profileId: 'test-profile',
      capabilities: ['chat.list'],
    })

    socket.write(clientFrame(hello.slice(0, 31), 1, false))
    socket.write(clientFrame(hello.slice(31), 0, true))

    await waitFor(() => daemon.status().extensionVersion === '1.0.0')
    expect(daemon.status()).toMatchObject({
      extensionConnected: true,
      extensionVersion: '1.0.0',
      protocolVersion: 1,
      profiles: [expect.objectContaining({ profileId: 'test-profile' })],
    })
    socket.destroy()
  })
})

describe('image download helpers', () => {
  it('extractImageUrls parses markdown image URLs from replies', () => {
    const result = {
      data: {
        replies: [
          { content: '这里是一只布偶猫\n\n![image](https://cdn.byteimg.com/cat1.jpg?x-expires=123)\n\n![image](https://cdn.byteimg.com/cat2.png)' },
          { content: '文字回复，没有图片' },
        ],
      },
    }
    expect(extractImageUrls(result)).toEqual([
      'https://cdn.byteimg.com/cat1.jpg?x-expires=123',
      'https://cdn.byteimg.com/cat2.png',
    ])
  })

  it('extractImageUrls returns empty array when no replies', () => {
    expect(extractImageUrls({ data: {} })).toEqual([])
    expect(extractImageUrls({ data: { replies: [] } })).toEqual([])
    expect(extractImageUrls({ data: { replies: [{ content: 'no images' }] } })).toEqual([])
  })

  it('getImageExtension detects extension from URL path', () => {
    expect(getImageExtension('https://cdn.example.com/photo.jpg', null)).toBe('.jpg')
    expect(getImageExtension('https://cdn.example.com/photo.png?x-expires=123', null)).toBe('.png')
    expect(getImageExtension('https://cdn.example.com/photo', 'image/jpeg')).toBe('.jpg')
    expect(getImageExtension('https://cdn.example.com/photo', 'image/png')).toBe('.png')
    expect(getImageExtension('https://cdn.example.com/photo', null)).toBe('.png')
    expect(getImageExtension('https://cdn.example.com/photo', 'image/webp')).toBe('.webp')
    expect(getImageExtension('https://cdn.example.com/photo.avif', null)).toBe('.avif')
  })
})

async function waitFor(predicate, timeoutMs = 1_000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 10))
  }
  throw new Error('waitFor timed out')
}

function openRawWebSocket(port, path) {
  return new Promise((resolve, reject) => {
    const socket = connect(port, '127.0.0.1')
    socket.once('error', reject)
    socket.once('connect', () => {
      socket.write([
        `GET ${path} HTTP/1.1`,
        'Host: 127.0.0.1',
        'Upgrade: websocket',
        'Connection: Upgrade',
        'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
        'Sec-WebSocket-Version: 13',
        '',
        '',
      ].join('\r\n'))
    })
    let response = ''
    socket.on('data', chunk => {
      response += chunk.toString('utf8')
      if (!response.includes('\r\n\r\n')) return
      socket.off('error', reject)
      resolve(socket)
    })
  })
}

function clientFrame(data, opcode, fin) {
  const payload = Buffer.from(data)
  const mask = Buffer.from([0x12, 0x34, 0x56, 0x78])
  const length = payload.length
  const header = length < 126
    ? Buffer.from([(fin ? 0x80 : 0) | opcode, 0x80 | length])
    : Buffer.from([(fin ? 0x80 : 0) | opcode, 0x80 | 126, length >> 8, length & 0xff])
  const masked = Buffer.from(payload)
  for (let index = 0; index < masked.length; index += 1) masked[index] ^= mask[index % 4]
  return Buffer.concat([header, mask, masked])
}
