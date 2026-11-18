type IconName = 'wave' | 'upload' | 'file' | 'play' | 'stop' | 'reset' | 'copy' | 'check' | 'cpu' | 'arrow' | 'globe';
const paths: Record<IconName, string> = {
  wave: 'M3 10v4m4-8v12m5-16v20m5-16v12m4-8v4',
  upload: 'M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5',
  file: 'M14 2H5a1 1 0 0 0-1 1v18a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8l-6-6Zm0 0v6h6M8 13h8m-8 4h5',
  play: 'm8 4 13 8-13 8V4Z',
  stop: 'M5 5h14v14H5Z',
  reset: 'M3 10a9 9 0 1 1 2 8M3 4v6h6',
  copy: 'M9 9h12v12H9ZM15 5V3H3v12h2',
  check: 'm5 12 4 4L19 6',
  cpu: 'M7 7h10v10H7Zm-4 2h4m-4 6h4m10-6h4m-4 6h4M9 3v4m6-4v4m-6 10v4m6-4v4',
  arrow: 'M5 12h14m-5-5 5 5-5 5',
  globe: 'M21 12A9 9 0 1 1 3 12a9 9 0 0 1 18 0ZM3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z',
};
export function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
