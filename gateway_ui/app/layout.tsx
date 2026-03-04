"use client";

import "./globals.css";
import { useEffect, useState, useCallback } from "react";
import Sidebar from "@/components/sidebar";
import { connectGatewayWS, type GatewayEvent } from "@/lib/api";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    const ws = connectGatewayWS(
      (event: GatewayEvent) => {
        if (event.type === "connected") setConnected(true);
      },
      () => {
        setConnected(false);
        setTimeout(connect, 3000);
      }
    );
    ws.onopen = () => setConnected(true);
  }, []);

  useEffect(() => {
    connect();
  }, [connect]);

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
