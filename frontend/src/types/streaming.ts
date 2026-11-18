export type Language = 'English' | 'Chinese' | 'Indonesian';
export type StreamStatus = 'idle' | 'connecting' | 'ready' | 'streaming' | 'processing' | 'completed' | 'cancelled' | 'error';

export interface WavAudio {
  file: File;
  sampleRate: 16000;
  channels: 1;
  bitDepth: 16;
  dataOffset: number;
  dataBytes: number;
  durationSeconds: number;
}

export interface StartMessage {
  type: 'start';
  language: Language;
  sample_rate: 16000;
  channels: 1;
  bit_depth: 16;
  chunk_duration_ms: 500;
}

export interface SegmentEvent {
  type: 'segment_transcript';
  stream_id: string;
  segment_index: number;
  language: Language;
  text: string;
  audio_start_seconds: number;
  audio_end_seconds: number;
  inference_time_seconds: number;
  queue_wait_time_seconds: number;
}

export interface WindowMetrics {
  segment_index: number;
  inference_time_seconds: number;
  queue_wait_time_seconds: number;
}

export interface MetricsEvent {
  type: 'metrics';
  stream_id: string;
  time_to_first_transcript_seconds: number;
  last_audio_to_final_seconds: number;
  stream_duration_seconds: number;
  total_audio_duration_seconds: number;
  total_inference_time_seconds: number;
  total_queue_wait_time_seconds: number;
  windows: WindowMetrics[];
}

export type ServerEvent =
  | { type: 'ready'; message: string; stream_id: string }
  | SegmentEvent
  | { type: 'final_transcript'; stream_id: string; language: Language; text: string }
  | MetricsEvent
  | { type: 'completed'; stream_id: string }
  | { type: 'cancelled'; stream_id: string; message: string }
  | { type: 'error'; stream_id?: string; code: string; message: string };

export interface Health {
  status: string;
  model: string;
  device: string;
  model_loaded: boolean;
}

export interface SystemInfo {
  cpu_logical_processors: number | null;
  cpu_physical_cores: number | null;
  total_ram_bytes: number;
  python_version: string;
  process_ram_bytes: number;
  model: string;
  device: string;
}
