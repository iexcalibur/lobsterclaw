const API_BASE =
  process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:4400";

const WS_BASE =
  process.env.NEXT_PUBLIC_GATEWAY_WS || "ws://localhost:4400/ws/gateway";

// API key — read from env. In dev with no key set, leave blank (gateway
// runs in dev mode and accepts unauthenticated requests).
const API_KEY = process.env.NEXT_PUBLIC_GATEWAY_API_KEY || "";

// Build WebSocket URL, appending ?api_key= when a key is configured.
const WS_URL = API_KEY ? `${WS_BASE}?api_key=${encodeURIComponent(API_KEY)}` : WS_BASE;

export async function api<T = any>(
  path: string,
  opts?: RequestInit
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts?.headers as Record<string, string>),
  };
  // Attach API key when configured
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

export function postJSON<T = any>(path: string, body: object): Promise<T> {
  return api(path, { method: "POST", body: JSON.stringify(body) });
}

export type GatewayEvent = {
  type: string;
  data?: Record<string, any>;
  ts?: number;
};

export function connectGatewayWS(
  onMessage: (event: GatewayEvent) => void,
  onClose?: () => void
): WebSocket {
  const ws = new WebSocket(WS_URL);
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data));
    } catch {
      /* ignore parse errors */
    }
  };
  ws.onclose = () => onClose?.();
  ws.onerror = () => onClose?.();
  return ws;
}

export function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
