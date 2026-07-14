import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  Bot,
  MessageSquarePlus,
  MessagesSquare,
  Radio,
  Sparkles,
} from "lucide-react";
import { api } from "../lib/api";
import type { Me } from "../lib/types";

interface LauncherCard {
  key: string;
  title: string;
  description: string;
  icon: ReactNode;
  onClick: () => void;
}

interface Props {
  onNewTopic: () => void;
  onNewChat: () => void;
  onNewLive: () => void;
  onNewAgent: () => void;
  liveEnabled?: boolean;
}

/**
 * Landing surface shown at `/`. A calm launcher that lets the user pick which
 * kind of conversation to start — topic, chat, live session, or agent — instead
 * of dropping them straight into a mode. Each card mirrors the section it opens.
 */
export function HomePage({
  onNewTopic,
  onNewChat,
  onNewLive,
  onNewAgent,
  liveEnabled = true,
}: Props) {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.me
      .get()
      .then((m) => {
        if (!cancelled) setMe(m);
      })
      .catch(() => {
        /* greeting falls back to a generic label */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const firstName = (me?.github?.name || me?.github?.login || "").split(" ")[0];

  const cards: LauncherCard[] = [
    {
      key: "topic",
      title: "New topic",
      description:
        "A long-lived thread that keeps its own history, context, and optional linked issue.",
      icon: <MessagesSquare size={20} />,
      onClick: onNewTopic,
    },
    {
      key: "chat",
      title: "New chat",
      description:
        "A quick, throwaway conversation. Type a prompt and get going in seconds.",
      icon: <MessageSquarePlus size={20} />,
      onClick: onNewChat,
    },
    ...(liveEnabled
      ? [
          {
            key: "live",
            title: "New live session",
            description:
              "Capture a meeting live with transcription, notes, and summaries as it happens.",
            icon: <Radio size={20} />,
            onClick: onNewLive,
          } satisfies LauncherCard,
        ]
      : []),
    {
      key: "agent",
      title: "New agent",
      description:
        "Hand a task to an autonomous coding agent and follow its progress.",
      icon: <Bot size={20} />,
      onClick: onNewAgent,
    },
  ];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col justify-center gap-8 p-8">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-accent">
            <Sparkles size={18} />
            <span className="text-xs font-medium uppercase tracking-wide">
              Precursor
            </span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            {firstName ? `Hey ${firstName}!` : "Hey there!"}
          </h1>
          <p className="text-sm text-muted">
            What would you like to start?
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {cards.map((card) => (
            <button
              key={card.key}
              type="button"
              onClick={card.onClick}
              className="group flex flex-col gap-3 rounded-xl border border-border bg-surface/60 p-5 text-left transition-colors hover:border-accent/50 hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <div className="flex items-center justify-between">
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent">
                  {card.icon}
                </span>
                <ArrowRight
                  size={18}
                  className="text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-accent"
                />
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-sm font-medium">{card.title}</span>
                <span className="text-[12px] leading-relaxed text-muted">
                  {card.description}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
