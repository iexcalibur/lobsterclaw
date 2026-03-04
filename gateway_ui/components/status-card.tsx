import clsx from "clsx";
import type { LucideIcon } from "lucide-react";

interface StatusCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: LucideIcon;
  color?: "blue" | "green" | "yellow" | "red" | "purple" | "cyan";
}

const colorMap = {
  blue: "bg-blue-500/10 text-blue-400",
  green: "bg-emerald-500/10 text-emerald-400",
  yellow: "bg-yellow-500/10 text-yellow-400",
  red: "bg-red-500/10 text-red-400",
  purple: "bg-purple-500/10 text-purple-400",
  cyan: "bg-cyan-500/10 text-cyan-400",
};

export default function StatusCard({
  title,
  value,
  subtitle,
  icon: Icon,
  color = "blue",
}: StatusCardProps) {
  return (
    <div className="card">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
            {title}
          </p>
          <p className="mt-2 text-2xl font-semibold text-white">{value}</p>
          {subtitle && (
            <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
          )}
        </div>
        <div className={clsx("rounded-lg p-2", colorMap[color])}>
          <Icon className="h-4 w-4" />
        </div>
      </div>
    </div>
  );
}
