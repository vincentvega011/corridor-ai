import type { ReactNode } from "react";

/**
 * The icon set: 24 px outline icons drawn here, so nothing is fetched and nothing depends on an icon font.
 * Decorative by default (aria-hidden); the text next to an icon carries the meaning.
 */
const PATHS: Record<string, ReactNode> = {
  shield: <path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z" />,
  "shield-check": (<><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z" /><path d="M8.8 12.2l2.2 2.2 4.2-4.4" /></>),
  pencil: (<><path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 013 3L8 19l-4 1z" /><path d="M14.5 6.5l3 3" /></>),
  document: (<><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4" /><path d="M10 12h5M10 16h5" /></>),
  "clock-lock": (<><circle cx="11" cy="11" r="7.5" /><path d="M11 7v4.2l2.6 1.6" /><rect x="14.5" y="15.5" width="6" height="5" rx="1" /><path d="M16 15.5v-1.2a1.5 1.5 0 013 0v1.2" /></>),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  "check-circle": (<><circle cx="12" cy="12" r="9" /><path d="M8 12.3l2.8 2.8L16.2 9.5" /></>),
  hand: (<><path d="M8 12V6.5a1.5 1.5 0 013 0V11" /><path d="M11 10.5V5a1.5 1.5 0 013 0v6" /><path d="M14 10.5V6.5a1.5 1.5 0 013 0V14c0 4-2.2 7-6 7-3 0-4.5-1.7-6-4.2L3.8 13a1.5 1.5 0 012.5-1.6L8 13.5" /></>),
  keypad: (<><circle cx="6" cy="6" r="1.4" /><circle cx="12" cy="6" r="1.4" /><circle cx="18" cy="6" r="1.4" /><circle cx="6" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="18" cy="12" r="1.4" /><circle cx="12" cy="18" r="1.4" /></>),
  "octagon-x": (<><path d="M8.2 3h7.6L21 8.2v7.6L15.8 21H8.2L3 15.8V8.2z" /><path d="M9 9l6 6M15 9l-6 6" /></>),
  chat: (<><path d="M4 5.5A2.5 2.5 0 016.5 3h11A2.5 2.5 0 0120 5.5v8a2.5 2.5 0 01-2.5 2.5H11l-4.5 4v-4h0A2.5 2.5 0 014 13.5z" /><path d="M8.5 8.5h7M8.5 11.5h4" /></>),
  payees: (<><circle cx="9" cy="8" r="3.2" /><path d="M3 20c.4-3.4 3-5.5 6-5.5s5.6 2.1 6 5.5" /><path d="M16.5 5.5a3 3 0 010 5.6M18 14.8c1.7.6 2.8 2.2 3 4.2" /></>),
  route: (<><circle cx="5.5" cy="18" r="2" /><circle cx="18.5" cy="6" r="2" /><path d="M7.5 18H15a3 3 0 000-6H9a3 3 0 010-6h7.5" /></>),
  tuition: (<><path d="M2.5 9.5L12 5l9.5 4.5L12 14z" /><path d="M6.5 11.8V16c0 1.2 2.5 2.5 5.5 2.5s5.5-1.3 5.5-2.5v-4.2" /><path d="M21.5 9.5V15" /></>),
  family: (<><circle cx="8" cy="7.5" r="2.7" /><circle cx="17" cy="9" r="2.2" /><path d="M2.8 19c.3-3 2.4-5 5.2-5s4.9 2 5.2 5" /><path d="M14.5 14.3c.8-.4 1.6-.6 2.5-.6 2.3 0 4 1.7 4.2 4.3" /></>),
  passport: (<><rect x="4" y="3" width="16" height="18" rx="2.5" /><circle cx="12" cy="10.5" r="3" /><path d="M9.5 10.5h5M12 7.5c-1.2 1.4-1.2 4.6 0 6M12 7.5c1.2 1.4 1.2 4.6 0 6" /><path d="M8 17.5h8" /></>),
  badge: (<><path d="M12 3l2.4 1.7 2.9-.2 1 2.7 2.4 1.7-.9 2.8.9 2.8-2.4 1.7-1 2.7-2.9-.2L12 21l-2.4-1.7-2.9.2-1-2.7-2.4-1.7.9-2.8-.9-2.8 2.4-1.7 1-2.7 2.9.2z" /><path d="M8.8 12.2l2.2 2.2 4.2-4.4" /></>),
  more: (<><circle cx="5.5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="18.5" cy="12" r="1.6" /></>),
  plus: <path d="M12 5v14M5 12h14" />,
  send: <path d="M4 12l16-8-5.5 16-3-6.5z" />,
  paperclip: <path d="M19.5 11.5l-7.6 7.6a4.6 4.6 0 01-6.5-6.5L13 5a3.1 3.1 0 014.4 4.4l-7.5 7.5a1.5 1.5 0 01-2.2-2.2l6.8-6.8" />,
  lock: (<><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8 10.5V8a4 4 0 018 0v2.5" /></>),
  info: (<><circle cx="12" cy="12" r="9" /><path d="M12 11v5.5M12 7.8v.1" /></>),
  flask: (<><path d="M9.5 3h5M10.5 3v6L5 19a1.6 1.6 0 001.4 2.3h11.2A1.6 1.6 0 0019 19l-5.5-10V3" /><path d="M7.5 15h9" /></>),
  alert: (<><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.2v.1" /></>),
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  "arrow-down": <path d="M12 5v14M6 13l6 6 6-6" />,
  calendar: (<><rect x="4" y="5" width="16" height="15" rx="2.5" /><path d="M4 10h16M8.5 3v4M15.5 3v4" /></>),
  bank: (<><path d="M3 9l9-5 9 5" /><path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20.5h18" /></>),
  star: <path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9 6.8 19.7l1-5.9L3.5 9.7l5.9-.8z" />,
  copy: (<><rect x="8.5" y="8.5" width="11" height="11" rx="2" /><path d="M15.5 8.5V6.5a2 2 0 00-2-2h-7a2 2 0 00-2 2v7a2 2 0 002 2h2" /></>),
  sun: (<><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" /></>),
  moon: <path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z" />,
  globe: (<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" /></>),
  user: (<><circle cx="12" cy="8" r="3.6" /><path d="M4.5 20c.5-3.8 3.6-6 7.5-6s7 2.2 7.5 6" /></>),
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, className, title }: { name: IconName; size?: number; className?: string; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}
      {PATHS[name]}
    </svg>
  );
}
