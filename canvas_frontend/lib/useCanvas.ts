"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { CanvasState, ClientMessage, ServerMessage } from "./types";

const CANVAS_HOST_WS =
  process.env.NEXT_PUBLIC_CANVAS_HOST_WS_URL || "ws://localhost:7681";

const RECONNECT_DELAY_MS = 2000;
const MAX_RECONNECT_DELAY_MS = 30_000;

export function useCanvas(sessionId: string): {
  state: CanvasState;
  sendEvent: (msg: Omit<ClientMessage, "sessionId">) => void;
} {
  const [state, setState] = useState<CanvasState>({
    sessionId,
    content: null,
    visible: true,
    title: "",
    connected: false,
    lastEventAt: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelayRef = useRef(RECONNECT_DELAY_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  const sendEvent = useCallback(
    (msg: Omit<ClientMessage, "sessionId">) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ ...msg, sessionId }));
    },
    [sessionId]
  );

  useEffect(() => {
    unmountedRef.current = false;

    function connect() {
      if (unmountedRef.current) return;

      const url = `${CANVAS_HOST_WS}/ws/${sessionId}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmountedRef.current) { ws.close(); return; }
        reconnectDelayRef.current = RECONNECT_DELAY_MS;
        setState((s) => ({ ...s, connected: true }));
        // Announce ourselves
        ws.send(JSON.stringify({ type: "ready", sessionId }));
      };

      ws.onmessage = (evt) => {
        let msg: ServerMessage;
        try {
          msg = JSON.parse(evt.data as string);
        } catch {
          return;
        }

        switch (msg.type) {
          case "present":
            setState((s) => ({
              ...s,
              content: msg.content,
              visible: true,
              title: msg.title || s.title,
              lastEventAt: Date.now(),
            }));
            break;

          case "restore":
            setState((s) => ({
              ...s,
              content: msg.content,
              url: undefined,
              visible: msg.visible,
              title: msg.title || s.title,
            }));
            break;

          case "navigate":
            setState((s) => ({
              ...s,
              content: { kind: "url", url: msg.url },
              visible: true,
              lastEventAt: Date.now(),
            }));
            break;

          case "hide":
            setState((s) => ({ ...s, visible: false }));
            break;

          case "show":
            setState((s) => ({ ...s, visible: true }));
            break;

          case "eval": {
            // Execute script and send result back
            let result: unknown;
            try {
              // eslint-disable-next-line no-eval
              result = eval(msg.script);
            } catch (e) {
              result = String(e);
            }
            ws.send(
              JSON.stringify({
                type: "eval_result",
                id: msg.id,
                result,
                sessionId,
              })
            );
            break;
          }

          case "update":
            setState((s) => {
              if (!s.content) return s;
              return {
                ...s,
                content: { ...s.content, ...msg.patch } as typeof s.content,
                lastEventAt: Date.now(),
              };
            });
            break;

          case "close":
            setState((s) => ({ ...s, content: null, connected: false }));
            ws.close();
            break;

          default:
            break;
        }
      };

      ws.onclose = () => {
        if (unmountedRef.current) return;
        setState((s) => ({ ...s, connected: false }));
        wsRef.current = null;

        // Exponential backoff reconnect
        const delay = reconnectDelayRef.current;
        reconnectDelayRef.current = Math.min(delay * 2, MAX_RECONNECT_DELAY_MS);
        reconnectTimerRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [sessionId]);

  return { state, sendEvent };
}
