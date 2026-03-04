"use client";

import { useEffect, useState } from "react";
import { Zap, Star, FileText } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

interface Skill {
  name: string;
  description: string;
  always: boolean;
  path: string;
  content_preview: string;
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await api<{ skills: Skill[]; total: number }>(
          "/api/gateway/skills"
        );
        setSkills(res.skills);
      } catch {
        /* retry */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const alwaysOn = skills.filter((s) => s.always);
  const onDemand = skills.filter((s) => !s.always);

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-semibold">Skills</h1>
        <p className="text-sm text-zinc-500 mt-1">
          {skills.length} skills loaded &middot; {alwaysOn.length} always-active
        </p>
      </div>

      {loading ? (
        <div className="card text-center text-zinc-500 text-sm py-12">
          Loading skills...
        </div>
      ) : skills.length === 0 ? (
        <div className="card text-center py-16">
          <Zap className="h-10 w-10 text-zinc-600 mx-auto mb-3" />
          <p className="text-zinc-500 text-sm">
            No skills found. Add skills to{" "}
            <code className="text-xs bg-zinc-800 px-1.5 py-0.5 rounded">
              workspace/skills/
            </code>
          </p>
        </div>
      ) : (
        <>
          {/* Always-active skills */}
          {alwaysOn.length > 0 && (
            <div>
              <h2 className="text-sm font-medium text-zinc-400 mb-3 flex items-center gap-1.5">
                <Star className="h-3.5 w-3.5 text-yellow-400" />
                Always Active
              </h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {alwaysOn.map((skill) => (
                  <SkillCard
                    key={skill.name}
                    skill={skill}
                    expanded={expanded === skill.name}
                    onToggle={() =>
                      setExpanded(
                        expanded === skill.name ? null : skill.name
                      )
                    }
                  />
                ))}
              </div>
            </div>
          )}

          {/* On-demand skills */}
          {onDemand.length > 0 && (
            <div>
              <h2 className="text-sm font-medium text-zinc-400 mb-3 flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5" />
                On Demand ({onDemand.length})
              </h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {onDemand.map((skill) => (
                  <SkillCard
                    key={skill.name}
                    skill={skill}
                    expanded={expanded === skill.name}
                    onToggle={() =>
                      setExpanded(
                        expanded === skill.name ? null : skill.name
                      )
                    }
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SkillCard({
  skill,
  expanded,
  onToggle,
}: {
  skill: Skill;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className={clsx(
        "card-hover text-left w-full",
        expanded && "ring-1 ring-zinc-700"
      )}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <Zap
            className={clsx(
              "h-4 w-4",
              skill.always ? "text-yellow-400" : "text-zinc-500"
            )}
          />
          <h3 className="text-sm font-medium text-white">{skill.name}</h3>
        </div>
        {skill.always && <span className="badge-yellow">always</span>}
      </div>
      {skill.description && (
        <p className="text-xs text-zinc-500 mb-2">{skill.description}</p>
      )}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-zinc-800">
          <p className="text-xs text-zinc-400 whitespace-pre-wrap font-mono leading-relaxed">
            {skill.content_preview}
          </p>
          <p className="mt-2 text-[10px] text-zinc-600 truncate">
            {skill.path}
          </p>
        </div>
      )}
    </button>
  );
}
