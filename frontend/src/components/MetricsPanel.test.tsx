import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { SystemInfo } from '../types/streaming';
import { MetricsPanel } from './MetricsPanel';

afterEach(cleanup);

function show(system: Partial<SystemInfo> | null) {
  render(<MetricsPanel metrics={null} segments={[]} system={system as SystemInfo | null} refreshedAt={null} />);
}

describe('dashboard CPU values', () => {
  it('shows separate system and unnormalized backend measurements', () => {
    show({ system_cpu_percent: 43.25, process_cpu_percent: 321.7, process_ram_bytes: 1024 ** 2 });
    expect(screen.getByText('43.3%')).toBeTruthy();
    expect(screen.getByText('321.7%')).toBeTruthy();
  });

  it('displays a measured zero as zero', () => {
    show({ system_cpu_percent: 0, process_cpu_percent: 0 });
    expect(screen.getAllByText('0.0%')).toHaveLength(2);
  });

  it('shows unavailable for missing or invalid measurements', () => {
    show({ system_cpu_percent: null, process_cpu_percent: Number.NaN });
    expect(screen.getAllByText('Unavailable')).toHaveLength(2);
  });
});
