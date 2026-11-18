/** Complete React tree with real hooks/parser; network, ASR, and clipboard are mocks. */
import { File as NodeFile } from 'node:buffer';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { Language } from './types/streaming';

class MockSocket {
  static OPEN = 1; static CONNECTING = 0;
  static instances: MockSocket[] = [];
  readyState = 0; bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  id = `mock-${MockSocket.instances.length}`;
  send = vi.fn();
  close = vi.fn(() => { this.readyState = 3; });
  constructor(readonly url: string) { MockSocket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  receive(event: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify({ stream_id: this.id, ...event }) }); }
}

function fixtureWav(): File {
  // A silent fixture only, not a real-language recording or reference.
  const bytes = new Uint8Array(44 + 112000), header = new DataView(bytes.buffer);
  const text = (offset: number, value: string) => bytes.set(new TextEncoder().encode(value), offset);
  text(0, 'RIFF'); header.setUint32(4, bytes.length - 8, true); text(8, 'WAVE'); text(12, 'fmt ');
  header.setUint32(16, 16, true); header.setUint16(20, 1, true); header.setUint16(22, 1, true);
  header.setUint32(24, 16000, true); header.setUint32(28, 32000, true);
  header.setUint16(32, 2, true); header.setUint16(34, 16, true); text(36, 'data'); header.setUint32(40, 112000, true);
  return new NodeFile([bytes], 'fixture.wav', { type: 'audio/wav' }) as unknown as File;
}

const speech: Record<Language, [string, string]> = {
  English: ['Hello everyone.', 'This is a speech test.'],
  Chinese: ['大家好，', '这是一次语音测试。'],
  Indonesian: ['Halo semuanya.', 'Ini adalah uji pengenalan suara.'],
};
const labels: Record<Language, string> = { English: 'English', Chinese: 'Mandarin Chinese', Indonesian: 'Bahasa Indonesia' };
const clipboard = vi.fn();
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
const tick = async (ms = 0) => { await act(() => vi.advanceTimersByTimeAsync(ms)); };

async function setup() {
  render(<App />); await tick();
  fireEvent.change(screen.getByTestId('audio-file'), { target: { files: [fixtureWav()] } }); await tick();
  expect(screen.getByTestId('audio-metadata').textContent).toContain('fixture.wav');
}

function start(language: Language = 'English') {
  fireEvent.change(screen.getByLabelText('Recognition language'), { target: { value: language } });
  fireEvent.click(screen.getByRole('button', { name: 'Start Streaming' }));
  const socket = MockSocket.instances.at(-1)!;
  act(() => socket.open());
  expect(JSON.parse(socket.send.mock.calls[0][0]).language).toBe(language);
  act(() => socket.receive({ type: 'ready', message: 'Session started' }));
  return socket;
}

function segment(socket: MockSocket, language: Language, index: number) {
  socket.receive({ type: 'segment_transcript', segment_index: index, language, text: speech[language][index],
    audio_start_seconds: index * 3, audio_end_seconds: index === 0 ? 3 : 3.5,
    inference_time_seconds: .2, queue_wait_time_seconds: .01 });
}

describe('multilingual React integration with mocked transport', () => {
  beforeEach(() => {
    vi.useFakeTimers(); MockSocket.instances = []; vi.stubGlobal('WebSocket', MockSocket);
    clipboard.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: clipboard }, configurable: true });
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200,
      json: async () => url.endsWith('/health')
        ? { status: 'healthy', model: 'Qwen3-ASR-0.6B', device: 'cpu', model_loaded: true }
        : { process_ram_bytes: 1000000, total_ram_bytes: 16000000000, device: 'cpu' },
    })));
  });
  afterEach(() => {
    cleanup(); vi.useRealTimers(); vi.unstubAllGlobals();
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
    else Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('switches all three labels, sends PCM progressively, deduplicates, copies Unicode, and resets', async () => {
    await setup();
    const options = Array.from((screen.getByLabelText('Recognition language') as HTMLSelectElement).options);
    expect(options.map(option => [option.text, option.value])).toEqual(Object.entries(labels).map(([value, label]) => [label, value]));
    for (const language of Object.keys(speech) as Language[]) {
      const socket = start(language); await tick(3000);
      const chunks = socket.send.mock.calls.filter(([value]) => typeof value !== 'string');
      expect(chunks).toHaveLength(6); expect(chunks.every(([value]) => value.byteLength === 16000)).toBe(true);
      act(() => { segment(socket, language, 0); segment(socket, language, 0); });
      expect(screen.getByTestId('transcript').textContent).toBe(speech[language][0]);
      expect(screen.getByTestId('stream-status').textContent).toBe('Sending audio');
      await tick(500);
      expect(socket.send.mock.calls.at(-1)?.[0]).toBe('{"type":"end"}');
      expect(screen.getByTestId('stream-status').textContent).toBe('Waiting for remaining transcription');
      const final = speech[language].join(language === 'Chinese' ? '' : ' ');
      act(() => {
        segment(socket, language, 1);
        socket.receive({ type: 'final_transcript', language, text: final });
        socket.receive({ type: 'metrics', time_to_first_transcript_seconds: 3.2, last_audio_to_final_seconds: .2,
          stream_duration_seconds: 3.7, total_audio_duration_seconds: 3.5, total_inference_time_seconds: .4,
          total_queue_wait_time_seconds: .02, windows: [] });
        socket.receive({ type: 'completed' });
      });
      expect(screen.getByTestId('transcript').textContent).toBe(final);
      expect(screen.getByText('2 segments received')).toBeTruthy();
      expect(screen.getByTestId('stream-status').textContent).toBe('Transcription completed');
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy transcript' })); });
      expect(clipboard).toHaveBeenLastCalledWith(final); expect(socket.close).toHaveBeenCalled();
    }
    expect(MockSocket.instances).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(screen.queryByTestId('audio-metadata')).toBeNull();
    expect(screen.getByTestId('stream-status').textContent).toBe('Ready when you are');
  });

  it('Stop cancels transmission without sending end', async () => {
    await setup(); const socket = start('Chinese'); await tick(500);
    fireEvent.click(screen.getByRole('button', { name: 'Stop Streaming' })); await tick(5000);
    expect(screen.getByTestId('stream-status').textContent).toBe('Streaming cancelled');
    expect(socket.send.mock.calls.some(([value]) => value === '{"type":"stop"}')).toBe(true);
    expect(socket.send.mock.calls.some(([value]) => value === '{"type":"end"}')).toBe(false);
  });

  it('rejects a renamed non-WAV before opening a socket', async () => {
    render(<App />); await tick();
    const file = new NodeFile(['not a real WAV recording'], 'fake.wav') as unknown as File;
    fireEvent.change(screen.getByTestId('audio-file'), { target: { files: [file] } }); await tick();
    expect(screen.getByRole('alert').textContent).toContain('RIFF/WAVE');
    expect((screen.getByRole('button', { name: 'Start Streaming' }) as HTMLButtonElement).disabled).toBe(true);
    expect(MockSocket.instances).toHaveLength(0);
  });

  it('reports disconnect and retains partial Indonesian text', async () => {
    await setup(); const socket = start('Indonesian'); await tick(3000);
    act(() => segment(socket, 'Indonesian', 0)); act(() => socket.onclose?.({ code: 1006 }));
    expect(screen.getByRole('alert').textContent).toContain('disconnected');
    expect(screen.getByTestId('transcript').textContent).toBe(speech.Indonesian[0]);
  });

  it.each(['model_unavailable', 'inference_failed'])('shows %s', async code => {
    await setup(); const socket = start();
    act(() => socket.receive({ type: 'error', code, message: 'Mocked model error' }));
    expect(screen.getByRole('alert').textContent).toContain(code);
    expect(screen.getByTestId('stream-status').textContent).toBe('Session interrupted');
  });

  it('reports an offline backend and disables Start', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Network failed'));
    render(<App />); await tick(); expect(screen.getByText('Backend offline')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Start Streaming' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
