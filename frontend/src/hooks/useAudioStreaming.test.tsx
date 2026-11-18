import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { joinSegments, mergeSegment, useAudioStreaming } from './useAudioStreaming';
import type { MetricsEvent, SegmentEvent, WavAudio } from '../types/streaming';

class MockSocket {
  static OPEN = 1; static CONNECTING = 0; static CLOSED = 3;
  static instances: MockSocket[] = [];
  readyState = 0; bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => { this.readyState = 3; });
  constructor(readonly url: string) { MockSocket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  receive(event: unknown) { this.onmessage?.({ data: JSON.stringify(event) }); }
}

const audio: WavAudio = { file: { slice: (start: number, end: number) => ({ arrayBuffer: async () => new ArrayBuffer(end - start) }) } as unknown as File,
  sampleRate: 16000, channels: 1, bitDepth: 16, dataOffset: 80, dataBytes: 32000, durationSeconds: 1 };
const segment = (i = 0, text = 'Hello'): SegmentEvent => ({ type: 'segment_transcript', stream_id: 'session',
  segment_index: i, language: 'English', text, audio_start_seconds: i * 3, audio_end_seconds: (i + 1) * 3,
  inference_time_seconds: .2, queue_wait_time_seconds: .01 });
const metrics: MetricsEvent = { type: 'metrics', stream_id: 'session', time_to_first_transcript_seconds: 1,
  last_audio_to_final_seconds: .2, stream_duration_seconds: 1.2, total_audio_duration_seconds: 1,
  total_inference_time_seconds: .2, total_queue_wait_time_seconds: .01, windows: [] };

describe('transcript identity and ordering', () => {
  it('sorts segment indexes and ignores identical duplicates', () => {
    let segments = mergeSegment([], segment(1, 'world'));
    segments = mergeSegment(segments, segment(0));
    segments = mergeSegment(segments, segment(0));
    expect(segments).toHaveLength(2);
    expect(joinSegments(segments, 'English')).toBe('Hello world');
  });
  it('joins Chinese without inserted spaces', () => {
    expect(joinSegments([segment(0, '你好'), segment(1, '世界')], 'Chinese')).toBe('你好世界');
  });
  it('rejects conflicting repeated segments', () => {
    expect(() => mergeSegment([segment()], segment(0, 'Different'))).toThrow(/conflicting/);
  });
});

describe('stream lifecycle', () => {
  beforeEach(() => { vi.useFakeTimers(); MockSocket.instances = []; vi.stubGlobal('WebSocket', MockSocket); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
  function setup() {
    const hook = renderHook(() => useAudioStreaming());
    act(() => hook.result.current.start(audio, 'English'));
    const socket = MockSocket.instances[0];
    act(() => socket.open());
    return { ...hook, socket };
  }
  function ready(socket: MockSocket) { act(() => socket.receive({ type: 'ready', stream_id: 'session', message: 'started' })); }

  it('creates one connection and waits for ready before sending PCM', async () => {
    const hook = setup(); hook.rerender();
    act(() => hook.result.current.start(audio, 'English'));
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(MockSocket.instances).toHaveLength(1);
    expect(JSON.parse(hook.socket.send.mock.calls[0][0])).toEqual({ type: 'start', language: 'English', sample_rate: 16000, channels: 1, bit_depth: 16, chunk_duration_ms: 500 });
    expect(hook.socket.send).toHaveBeenCalledTimes(1);
    hook.unmount();
  });
  it('receives progressive text, sends end, and retains final text after completion', async () => {
    const { result, socket, unmount } = setup(); ready(socket);
    await act(() => vi.advanceTimersByTimeAsync(500));
    act(() => { socket.receive(segment()); socket.receive(segment()); });
    expect(result.current.status).toBe('streaming'); expect(result.current.segments).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(result.current.status).toBe('processing'); expect(result.current.sentBytes).toBe(32000);
    expect(socket.send.mock.calls.at(-1)?.[0]).toBe('{"type":"end"}');
    act(() => {
      socket.receive({ type: 'final_transcript', stream_id: 'session', language: 'English', text: 'Hello final.' });
      socket.receive(metrics); socket.receive({ type: 'completed', stream_id: 'session' });
    });
    expect(result.current.status).toBe('completed'); expect(result.current.finalTranscript).toBe('Hello final.');
    expect(socket.close).toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0); unmount();
  });
  it('stop sends stop, clears timers, and never sends end', async () => {
    const { result, socket, unmount } = setup(); ready(socket);
    await act(() => vi.advanceTimersByTimeAsync(500));
    act(() => result.current.stop());
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(result.current.status).toBe('cancelled');
    expect(socket.send.mock.calls.some(([value]) => value === '{"type":"stop"}')).toBe(true);
    expect(socket.send.mock.calls.some(([value]) => value === '{"type":"end"}')).toBe(false);
    expect(vi.getTimerCount()).toBe(0); unmount();
  });
  it('reset clears completed or partial data', () => {
    const { result, socket, unmount } = setup(); ready(socket);
    act(() => socket.receive(segment())); act(() => result.current.stop()); act(() => result.current.reset());
    expect(result.current.status).toBe('idle'); expect(result.current.segments).toEqual([]); unmount();
  });
  it('unmount cancels timers, closes the connection, and prevents further sends', async () => {
    const { socket, unmount } = setup(); ready(socket); unmount();
    const count = socket.send.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(socket.send).toHaveBeenCalledTimes(count); expect(socket.onmessage).toBeNull(); expect(vi.getTimerCount()).toBe(0);
  });
  it('reports disconnect while preserving partial text', () => {
    const { result, socket, unmount } = setup(); ready(socket);
    act(() => socket.receive(segment())); act(() => socket.onclose?.({ code: 1006 }));
    expect(result.current.status).toBe('error'); expect(result.current.segments).toHaveLength(1);
    expect(result.current.error).toContain('disconnected'); unmount();
  });
  it('displays model/inference errors', () => {
    const { result, socket, unmount } = setup();
    act(() => socket.receive({ type: 'error', code: 'model_unavailable', message: 'Model is not loaded.' }));
    expect(result.current.error).toContain('Model is not loaded'); expect(result.current.status).toBe('error'); unmount();
  });
  it('rejects unexpected events and premature completion', () => {
    const { result, socket, unmount } = setup(); ready(socket);
    act(() => socket.receive({ type: 'completed', stream_id: 'session' }));
    expect(result.current.status).toBe('error'); expect(result.current.error).toContain('without all ordered'); unmount();
  });
  it('reports a ready timeout and releases resources', async () => {
    const { result, socket, unmount } = setup();
    await act(() => vi.advanceTimersByTimeAsync(15000));
    expect(result.current.status).toBe('error'); expect(socket.close).toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0); unmount();
  });
});
