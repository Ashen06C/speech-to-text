import { useCallback, useEffect, useRef, useState } from 'react';
import { sendPcmChunks } from '../services/audioService';
import { decodeServerEvent, websocketUrl } from '../services/websocketService';
import type { Language, MetricsEvent, SegmentEvent, StartMessage, StreamStatus, WavAudio } from '../types/streaming';

interface StreamingState {
  status: StreamStatus;
  connected: boolean;
  segments: SegmentEvent[];
  finalTranscript: string | null;
  metrics: MetricsEvent | null;
  sentBytes: number;
  transmissionElapsed: number;
  error: string | null;
  streamId: string | null;
}

const initialState: StreamingState = {
  status: 'idle', connected: false, segments: [], finalTranscript: null,
  metrics: null, sentBytes: 0, transmissionElapsed: 0, error: null, streamId: null,
};

/** Index is the identity, so a repeated segment can never duplicate the text. */
export function mergeSegment(segments: SegmentEvent[], event: SegmentEvent): SegmentEvent[] {
  const existing = segments.find(segment => segment.segment_index === event.segment_index);
  if (existing) {
    if (existing.text !== event.text) throw new Error('The backend sent conflicting text for the same segment.');
    return segments;
  }
  return [...segments, event].sort((a, b) => a.segment_index - b.segment_index);
}

export function joinSegments(segments: SegmentEvent[], language: Language): string {
  return segments.map(segment => segment.text.trim()).filter(Boolean).join(language === 'Chinese' ? '' : ' ');
}

interface ActiveRun {
  socket: WebSocket;
  abort: AbortController;
  timer?: ReturnType<typeof setTimeout>;
  clock?: ReturnType<typeof setInterval>;
  streamId?: string;
  ready: boolean;
  endSent: boolean;
  segments: SegmentEvent[];
  final: boolean;
  metrics: boolean;
  terminal: boolean;
  language: Language;
}

function dispose(run: ActiveRun, sendStop = false): void {
  run.terminal = true;
  run.abort.abort();
  clearTimeout(run.timer);
  clearInterval(run.clock);
  const socket = run.socket;
  socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
  if (socket.readyState === WebSocket.OPEN) {
    if (sendStop && run.ready) socket.send(JSON.stringify({ type: 'stop' }));
    socket.close(1000, sendStop ? 'User cancelled' : 'Session closed');
  } else if (socket.readyState === WebSocket.CONNECTING) {
    socket.close();
  }
}

export function useAudioStreaming() {
  const [state, setState] = useState<StreamingState>(initialState);
  const current = useRef<ActiveRun | null>(null);

  const stop = useCallback(() => {
    const run = current.current;
    if (!run) return;
    dispose(run, true);
    current.current = null;
    setState(previous => ({ ...previous, status: 'cancelled', connected: false, error: null }));
  }, []);

  const reset = useCallback(() => {
    if (current.current) dispose(current.current, true);
    current.current = null;
    setState(initialState);
  }, []);

  const start = useCallback((audio: WavAudio, language: Language) => {
    if (current.current) return;
    setState({ ...initialState, status: 'connecting' });
    let socket: WebSocket;
    try { socket = new WebSocket(websocketUrl()); }
    catch (error) {
      setState({ ...initialState, status: 'error', error: error instanceof Error ? error.message : 'Unable to connect.' });
      return;
    }
    const run: ActiveRun = { socket, abort: new AbortController(), ready: false, endSent: false,
      segments: [], final: false, metrics: false, terminal: false, language };
    current.current = run;
    const isCurrent = () => current.current === run && !run.terminal;
    const fail = (message: string) => {
      if (!isCurrent()) return;
      dispose(run, true);
      current.current = null;
      setState(previous => ({ ...previous, status: 'error', connected: false, error: message }));
    };
    run.timer = setTimeout(() => fail('The backend did not become ready within 15 seconds. Check that the model is loaded and the URL is correct.'), 15000);

    socket.onopen = () => {
      if (!isCurrent()) return;
      const config: StartMessage = { type: 'start', language, sample_rate: 16000,
        channels: 1, bit_depth: 16, chunk_duration_ms: 500 };
      socket.send(JSON.stringify(config));
      setState(previous => ({ ...previous, connected: true }));
    };
    socket.onerror = () => fail('WebSocket connection failed. Check that the backend is running and supports /ws/transcribe.');
    socket.onclose = event => {
      if (!isCurrent()) return;
      fail(`The backend disconnected before completion (code ${event.code}). Your partial transcript is preserved.`);
    };
    socket.onmessage = message => {
      if (!isCurrent()) return;
      try {
        const event = decodeServerEvent(message.data);
        if (event.type === 'error') { fail(`${event.message} (${event.code})`); return; }
        if (event.type === 'ready') {
          if (run.ready) throw new Error('The backend sent ready more than once.');
          run.ready = true;
          run.streamId = event.stream_id;
          clearTimeout(run.timer);
          setState(previous => ({ ...previous, status: 'ready', streamId: event.stream_id }));
          const started = performance.now();
          run.clock = setInterval(() => {
            if (isCurrent()) setState(previous => ({ ...previous, transmissionElapsed: (performance.now() - started) / 1000 }));
          }, 100);
          void sendPcmChunks(audio, socket, run.abort.signal, (sentBytes, elapsed) => {
            if (isCurrent()) setState(previous => ({ ...previous, status: 'streaming', sentBytes, transmissionElapsed: elapsed }));
          }).then(() => {
            if (!isCurrent()) return;
            run.endSent = true;
            clearInterval(run.clock);
            setState(previous => ({ ...previous, status: 'processing' }));
          }).catch(error => {
            if (!run.abort.signal.aborted) fail(error instanceof Error ? error.message : 'Failed to transmit audio.');
          });
          return;
        }
        if (!run.ready || event.stream_id !== run.streamId) throw new Error('A backend response belongs to an unexpected streaming session.');
        if ('language' in event && event.language !== run.language) throw new Error('The backend returned an unexpected language.');
        if (event.type === 'segment_transcript') {
          if (run.final) throw new Error('The backend sent a segment after the final transcript.');
          run.segments = mergeSegment(run.segments, event);
          setState(previous => ({ ...previous, segments: run.segments }));
        } else if (event.type === 'final_transcript') {
          if (!run.endSent || run.final) throw new Error('Final transcript arrived in an unexpected state.');
          run.final = true;
          setState(previous => ({ ...previous, finalTranscript: event.text }));
        } else if (event.type === 'metrics') {
          if (!run.final || run.metrics) throw new Error('Metrics arrived in an unexpected state.');
          run.metrics = true;
          setState(previous => ({ ...previous, metrics: event }));
        } else if (event.type === 'completed') {
          if (!run.final || !run.metrics || run.segments.some((segment, i) => segment.segment_index !== i)) {
            throw new Error('The backend completed without all ordered transcript data and metrics.');
          }
          dispose(run);
          current.current = null;
          setState(previous => ({ ...previous, status: 'completed', connected: false }));
        } else if (event.type === 'cancelled') {
          dispose(run);
          current.current = null;
          setState(previous => ({ ...previous, status: 'cancelled', connected: false }));
        }
      } catch (error) {
        fail(error instanceof Error ? error.message : 'Unexpected WebSocket response.');
      }
    };
  }, []);

  useEffect(() => () => {
    if (current.current) dispose(current.current, true);
    current.current = null;
  }, []);

  return { ...state, start, stop, reset,
    busy: ['connecting', 'ready', 'streaming', 'processing'].includes(state.status) };
}
