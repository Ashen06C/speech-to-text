import { describe, expect, it } from 'vitest';
import { decodeServerEvent } from './websocketService';

describe('backend event protocol', () => {
  it('accepts ready and overload errors without a stream ID', () => {
    expect(decodeServerEvent('{"type":"ready","stream_id":"abc","message":"ready"}').type).toBe('ready');
    expect(decodeServerEvent('{"type":"error","code":"overloaded","message":"Retry"}').type).toBe('error');
  });
  it('accepts Unicode text without changing it', () => {
    const event = { type: 'final_transcript', stream_id: 'abc', language: 'Chinese', text: '你好，世界。' };
    expect(decodeServerEvent(JSON.stringify(event))).toEqual(event);
  });
  it.each(['{', '[]', '{"type":"unknown"}', '{"type":"ready"}',
    '{"type":"metrics","stream_id":"abc"}'])('rejects malformed events: %s', payload => {
    expect(() => decodeServerEvent(payload)).toThrow();
  });
});
