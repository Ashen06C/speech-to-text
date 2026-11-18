import type { Language, ServerEvent } from '../types/streaming';

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/backend').replace(/\/$/, '');
export function websocketUrl(): string {
  const url = new URL(import.meta.env.VITE_WS_URL || '/ws/transcribe', window.location.href);
  if (url.protocol === 'http:') url.protocol = 'ws:';
  if (url.protocol === 'https:') url.protocol = 'wss:';
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') throw new Error('WebSocket URL must use ws:// or wss://.');
  return url.toString();
}

const languages: Language[] = ['English', 'Chinese', 'Indonesian'];
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const nonnegative = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const index = (value: unknown) => nonnegative(value) && Number.isInteger(value);

/** Runtime validation protects the UI from a wrong backend URL or changed protocol. */
export function decodeServerEvent(payload: unknown): ServerEvent {
  if (typeof payload !== 'string') throw new Error('Unexpected binary response from the backend.');
  let event: unknown;
  try { event = JSON.parse(payload); } catch { throw new Error('The backend sent invalid JSON.'); }
  if (!record(event) || typeof event.type !== 'string') throw new Error('Unexpected WebSocket message.');
  const hasId = typeof event.stream_id === 'string' && event.stream_id.length > 0;
  const hasLanguage = languages.includes(event.language as Language);
  let valid = false;
  switch (event.type) {
    case 'ready': valid = hasId && typeof event.message === 'string'; break;
    case 'segment_transcript':
      valid = hasId && hasLanguage && index(event.segment_index) && typeof event.text === 'string' &&
        nonnegative(event.audio_start_seconds) && nonnegative(event.audio_end_seconds) &&
        event.audio_end_seconds >= event.audio_start_seconds && nonnegative(event.inference_time_seconds) &&
        nonnegative(event.queue_wait_time_seconds);
      break;
    case 'final_transcript': valid = hasId && hasLanguage && typeof event.text === 'string'; break;
    case 'metrics':
      valid = hasId && ['time_to_first_transcript_seconds', 'last_audio_to_final_seconds',
        'stream_duration_seconds', 'total_audio_duration_seconds', 'total_inference_time_seconds',
        'total_queue_wait_time_seconds'].every(key => nonnegative(event[key])) &&
        Array.isArray(event.windows) && event.windows.every(window => record(window) &&
          index(window.segment_index) && nonnegative(window.inference_time_seconds) && nonnegative(window.queue_wait_time_seconds));
      break;
    case 'completed': valid = hasId; break;
    case 'cancelled': valid = hasId && typeof event.message === 'string'; break;
    case 'error': valid = typeof event.code === 'string' && typeof event.message === 'string' &&
      (event.stream_id === undefined || hasId); break;
  }
  if (!valid) throw new Error(`Unexpected or invalid backend event: ${event.type}.`);
  return event as unknown as ServerEvent;
}
