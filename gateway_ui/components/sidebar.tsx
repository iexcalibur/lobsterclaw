"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import {
  LayoutDashboard,
  Bot,
  MessageSquare,
  Zap,
  Clock,
  Wrench,
  Settings,
  Activity,
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/skills", label: "Skills", icon: Zap },
  { href: "/cron", label: "Cron Jobs", icon: Clock },
  { href: "/tools", label: "Tools", icon: Wrench },
  { href: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  connected: boolean;
}

export default function Sidebar({ connected }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-56 flex-col border-r border-zinc-800 bg-zinc-950">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600">
          <Activity className="h-4 w-4 text-white" />
        </div>
        <div>
          <p className="text-sm font-semibold text-white">Pygate</p>
          <p className="text-[10px] text-zinc-500">Mission Control</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 px-3 py-2">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-zinc-800 text-white font-medium"
                  : "text-zinc-400 hover:text-white hover:bg-zinc-800/60"
              )}
            >
              <item.icon className="h-4 w-4 flex-shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Status */}
      <div className="border-t border-zinc-800 px-5 py-4">
        <div className="flex items-center gap-2">
          <div
            className={clsx(
              "h-2 w-2 rounded-full",
              connected ? "bg-emerald-400 pulse-dot" : "bg-red-400"
            )}
          />
          <span className="text-xs text-zinc-500">
            {connected ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>
    </aside>
  );
}
