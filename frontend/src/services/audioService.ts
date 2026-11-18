import type { WavAudio } from '../types/streaming';

const ascii = (view: DataView, offset: number, length: number) =>
  Array.from({ length }, (_, i) => String.fromCharCode(view.getUint8(offset + i))).join('');

async function read(file: Blob, offset: number, bytes: number): Promise<DataView> {
  return new DataView(await file.slice(offset, offset + bytes).arrayBuffer());
}

/** Walk RIFF chunks, including padding. Read headers, never the whole PCM payload. */
export async function parseWav(file: File): Promise<WavAudio> {
  if (file.size < 12) throw new Error('This file is too short to contain a WAV header.');
  const header = await read(file, 0, 12);
  if (ascii(header, 0, 4) !== 'RIFF' || ascii(header, 8, 4) !== 'WAVE') {
    throw new Error('Select a little-endian RIFF/WAVE audio file. Renaming a file to .wav is not enough.');
  }
  const riffEnd = header.getUint32(4, true) + 8;
  if (riffEnd > file.size || riffEnd < 12) throw new Error('The WAV container is truncated or has an invalid size.');
  let offset = 12;
  let format: { rate: number; channels: number; bits: number } | undefined;
  let dataOffset = -1;
  let dataBytes = 0;
  while (offset < riffEnd) {
    if (offset + 8 > riffEnd) throw new Error('Incomplete WAV chunk header.');
    const chunk = await read(file, offset, 8);
    const name = ascii(chunk, 0, 4);
    const size = chunk.getUint32(4, true);
    const body = offset + 8;
    const next = body + size + (size % 2);
    if (next > riffEnd) throw new Error(`The WAV ${name} chunk is truncated or missing padding.`);
    if (name === 'fmt ') {
      if (format) throw new Error('WAV files with multiple format chunks are not supported.');
      if (size < 16) throw new Error('Invalid WAV format chunk.');
      const fmt = await read(file, body, Math.min(size, 40));
      const encoding = fmt.getUint16(0, true);
      const channels = fmt.getUint16(2, true);
      const rate = fmt.getUint32(4, true);
      const byteRate = fmt.getUint32(8, true);
      const blockAlign = fmt.getUint16(12, true);
      const bits = fmt.getUint16(14, true);
      // WAVE_FORMAT_EXTENSIBLE is valid only with the exact PCM subtype GUID.
      if (encoding === 0xfffe) {
        const pcmGuid = [1, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113];
        if (size < 40 || fmt.getUint16(16, true) < 22 || fmt.getUint16(18, true) !== 16 ||
            pcmGuid.some((value, i) => fmt.getUint8(24 + i) !== value)) {
          throw new Error('WAV extensible audio must use signed 16-bit Linear PCM.');
        }
      } else if (encoding !== 1) {
        throw new Error('Only signed 16-bit Linear PCM is supported; compressed or float WAV is not accepted.');
      }
      if (rate !== 16000) throw new Error(`Sample rate is ${rate.toLocaleString()} Hz. Select a 16,000 Hz file; resampling is disabled.`);
      if (channels !== 1) throw new Error('Select mono audio (one channel). Stereo files are not supported.');
      if (bits !== 16) throw new Error('Audio must be signed 16-bit Linear PCM.');
      if (blockAlign !== 2 || byteRate !== 32000) throw new Error('WAV byte rate or sample alignment is invalid.');
      format = { rate, channels, bits };
    } else if (name === 'data') {
      if (dataOffset !== -1) throw new Error('Multiple WAV audio data chunks are not supported.');
      dataOffset = body;
      dataBytes = size;
    }
    offset = next;
  }
  if (!format || dataOffset === -1) throw new Error('The WAV file must contain both fmt and data chunks.');
  if (dataBytes === 0) throw new Error('The WAV file contains no audio samples.');
  if (dataBytes % 2) throw new Error('PCM data ends with an incomplete 16-bit sample.');
  return { file, sampleRate: 16000, channels: 1, bitDepth: 16,
    dataOffset, dataBytes, durationSeconds: dataBytes / 32000 };
}

export function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException('Cancelled', 'AbortError'));
    const abort = () => { clearTimeout(timer); reject(new DOMException('Cancelled', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, Math.max(0, ms));
    signal.addEventListener('abort', abort, { once: true });
  });
}

/** Absolute deadlines avoid adding read/handler time to every 500 ms interval. */
export async function sendPcmChunks(audio: WavAudio, socket: WebSocket, signal: AbortSignal,
  progress: (sentBytes: number, elapsed: number) => void): Promise<void> {
  const started = performance.now();
  let sent = 0;
  while (sent < audio.dataBytes) {
    const size = Math.min(16000, audio.dataBytes - sent);
    const pcm = await audio.file.slice(audio.dataOffset + sent, audio.dataOffset + sent + size).arrayBuffer();
    if (pcm.byteLength !== size) throw new Error('The selected audio file could not be read completely.');
    await abortableDelay(started + (sent + size) / 32 - performance.now(), signal);
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    if (socket.readyState !== WebSocket.OPEN) throw new Error('Connection closed while sending audio.');
    // Never build an unlimited browser-side send queue during a stalled network.
    if (socket.bufferedAmount > 4 * 16000) throw new Error('The connection is too slow to send audio safely. Please retry.');
    socket.send(pcm);
    sent += size;
    progress(sent, (performance.now() - started) / 1000);
  }
  if (!signal.aborted) socket.send(JSON.stringify({ type: 'end' }));
}
