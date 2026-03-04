"use client";

import "./globals.css";
import { useEffect, useRef, useState } from "react";
import Sidebar from "@/components/sidebar";
import { connectGatewayWS, type GatewayEvent } from "@/lib/api";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let unmounted = false;

    function connect() {
      if (unmounted) return;
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
      }
      if (retryRef.current) clearTimeout(retryRef.current);

      const ws = connectGatewayWS(
        (event: GatewayEvent) => {
          if (event.type === "connected") setConnected(true);
        },
        () => {
          setConnected(false);
          if (!unmounted) {
            retryRef.current = setTimeout(connect, 5000);
          }
        }
      );
      ws.onopen = () => setConnected(true);
      wsRef.current = ws;
    }

    connect();

    return () => {
      unmounted = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
      }
    };
  }, []);

  return (
    <html lang="en" className="dark">
      <body className="bg-zinc-950 text-zinc-50 antialiased">
        <Sidebar connected={connected} />
        <main className="ml-56 min-h-screen">
          <div className="mx-auto max-w-7xl px-6 py-6">{children}</div>
        </main>
      </body>
    </html>
  );
}
