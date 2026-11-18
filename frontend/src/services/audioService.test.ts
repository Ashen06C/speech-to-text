import { File as NodeFile } from 'node:buffer';
import { describe, expect, it, vi } from 'vitest';
import { parseWav, sendPcmChunks } from './audioService';

const text = (value: string) => new TextEncoder().encode(value);
function chunk(name: string, body: Uint8Array) {
  const chunk = new Uint8Array(8 + body.length + body.length % 2);
  chunk.set(text(name)); new DataView(chunk.buffer).setUint32(4, body.length, true); chunk.set(body, 8);
  return chunk;
}
export function wavFile(options: { rate?: number; channels?: number; bits?: number; encoding?: number;
  pcm?: Uint8Array; extra?: boolean; dataFirst?: boolean; extensible?: boolean } = {}): File {
  const fmt = new Uint8Array(options.extensible ? 40 : 16);
  const view = new DataView(fmt.buffer);
  const rate = options.rate ?? 16000, channels = options.channels ?? 1, bits = options.bits ?? 16;
  view.setUint16(0, options.extensible ? 0xfffe : options.encoding ?? 1, true);
  view.setUint16(2, channels, true); view.setUint32(4, rate, true);
  view.setUint32(8, rate * channels * bits / 8, true); view.setUint16(12, channels * bits / 8, true);
  view.setUint16(14, bits, true);
  if (options.extensible) {
    view.setUint16(16, 22, true); view.setUint16(18, 16, true);
    fmt.set([1, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113], 24);
  }
  const format = chunk('fmt ', fmt), data = chunk('data', options.pcm ?? new Uint8Array(32000));
  const chunks = options.dataFirst ? [data, format] : [format, ...(options.extra ? [chunk('JUNK', text('odd'))] : []), data];
  const result = new Uint8Array(12 + chunks.reduce((sum, value) => sum + value.length, 0));
  result.set(text('RIFF')); new DataView(result.buffer).setUint32(4, result.length - 8, true); result.set(text('WAVE'), 8);
  let offset = 12; for (const part of chunks) { result.set(part, offset); offset += part.length; }
  return new NodeFile([result], 'test.wav', { type: 'audio/wav' }) as unknown as File;
}

describe('actual WAV structure validation', () => {
  it('finds standard data and measures duration', async () => {
    expect(await parseWav(wavFile())).toMatchObject({ dataOffset: 44, dataBytes: 32000, durationSeconds: 1 });
  });
  it('handles metadata chunks and odd-length chunk padding', async () => {
    expect(await parseWav(wavFile({ extra: true }))).toMatchObject({ dataOffset: 56, durationSeconds: 1 });
  });
  it('finds fmt even if data appears first', async () => {
    expect(await parseWav(wavFile({ dataFirst: true }))).toMatchObject({ dataOffset: 20, durationSeconds: 1 });
  });
  it('accepts extensible PCM16 with the exact PCM GUID', async () => {
    expect((await parseWav(wavFile({ extensible: true }))).dataOffset).toBe(68);
  });
  it.each([
    [{ rate: 44100 }, /16,000/], [{ channels: 2 }, /mono/], [{ bits: 24 }, /16-bit/],
    [{ encoding: 3 }, /Linear PCM/], [{ pcm: new Uint8Array() }, /no audio/],
    [{ pcm: new Uint8Array(3) }, /incomplete/],
  ] as const)('rejects unsupported configuration %o', async (options, message) => {
    await expect(parseWav(wavFile(options))).rejects.toThrow(message);
  });
  it('rejects a renamed non-WAV file', async () => {
    const file = new NodeFile(['This is not WAV audio'], 'fake.wav') as unknown as File;
    await expect(parseWav(file)).rejects.toThrow(/RIFF/);
  });
  it('rejects truncated PCM data', async () => {
    const valid = wavFile();
    const file = new NodeFile([await valid.slice(0, valid.size - 2).arrayBuffer()], 'truncated.wav') as unknown as File;
    await expect(parseWav(file)).rejects.toThrow(/truncated/);
  });
  it('rejects files missing fmt/data chunks', async () => {
    const bytes = new Uint8Array(await wavFile().arrayBuffer()); bytes.set(text('JUNK'), 12);
    const file = new NodeFile([bytes], 'missing.wav') as unknown as File;
    await expect(parseWav(file)).rejects.toThrow(/fmt and data/);
  });
});

describe('paced raw PCM transmission', () => {
  it('sends only PCM in full chunks and a residual, then end', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', { OPEN: 1 });
    try {
      const pcm = new Uint8Array(34000).fill(19);
      const audio = await parseWav(wavFile({ extra: true, pcm }));
      const socket = { readyState: 1, bufferedAmount: 0, send: vi.fn() };
      const progress = vi.fn();
      const sending = sendPcmChunks(audio, socket as unknown as WebSocket, new AbortController().signal, progress);
      await vi.advanceTimersByTimeAsync(499); expect(socket.send).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1); expect(socket.send).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(500); expect(socket.send).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(63); await sending;
      expect(socket.send.mock.calls.map(([value]) => typeof value === 'string' ? value : value.byteLength)).toEqual([16000, 16000, 2000, '{"type":"end"}']);
      expect(new Uint8Array(socket.send.mock.calls[0][0] as ArrayBuffer).every(value => value === 19)).toBe(true);
      expect(progress.mock.calls.at(-1)?.[0]).toBe(34000);
    } finally { vi.useRealTimers(); vi.unstubAllGlobals(); }
  });
  it('cancellation prevents more chunks and end', async () => {
    vi.useFakeTimers(); vi.stubGlobal('WebSocket', { OPEN: 1 });
    try {
      const audio = await parseWav(wavFile());
      const socket = { readyState: 1, bufferedAmount: 0, send: vi.fn() };
      const abort = new AbortController();
      const sending = sendPcmChunks(audio, socket as unknown as WebSocket, abort.signal, vi.fn());
      const rejection = expect(sending).rejects.toThrow(/Cancelled/);
      abort.abort(); await rejection; expect(socket.send).not.toHaveBeenCalled();
    } finally { vi.useRealTimers(); vi.unstubAllGlobals(); }
  });
});
