"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";

import { Alert, Badge, Button, Empty, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { digestSummary, isDigest } from "@/lib/notifications";
import type { NotificationItem } from "@/lib/types";

function DigestItems({ id }: { id: string }) {
  const items = useQuery({
    queryKey: ["notifications", id, "items"],
    queryFn: () => api<NotificationItem[]>(`/v1/notifications/${id}/items`),
  });
  if (items.isPending) return <Spinner />;
  return (
    <ul className="mt-1 flex flex-col gap-1 border-l-2 border-stone-200 pl-2 dark:border-stone-700">
      {items.data?.map((item) => (
        <li key={item.id} className="text-xs">
          <span className="font-medium">{item.title}</span>
          {item.body && <span className="block text-stone-600 dark:text-stone-400">{item.body}</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * The notifications panel, a disclosure (button + panel, not an ARIA menu: it holds buttons
 * and text). Escape closes it and gives the focus back to the button; a click outside closes it.
 */
export function NotificationsMenu() {
  const queryClient = useQueryClient();
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<NotificationItem[]>("/v1/notifications"),
    refetchInterval: 60_000,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const mark = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "read" | "dismissed" }) =>
      api(`/v1/notifications/${id}`, { method: "PATCH", body: { status } }),
    onSuccess: refresh,
  });
  const readAll = useMutation({
    mutationFn: () => api("/v1/notifications/read-all", { method: "POST" }),
    onSuccess: refresh,
  });
  const count = notifications.data?.length ?? 0;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    // Escape closes it even when Tab has moved the focus out of the panel.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    // Positioned from sm only: on a phone the panel spans the header's width instead.
    <div
      ref={root}
      className="sm:relative"
    >
      <Button
        ref={trigger}
        variant="ghost"
        size="sm"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        Notifications
        {count > 0 && (
          <>
            <span aria-hidden>
              <Badge tone="teal">{count}</Badge>
            </span>
            <span className="sr-only">, {count} en attente</span>
          </>
        )}
      </Button>
      <div
        id={panelId}
        role="group"
        aria-label="Notifications"
        hidden={!open}
        className="absolute inset-x-4 top-full z-30 mt-1 rounded-lg border border-stone-200 bg-white p-2 shadow-lg sm:inset-x-auto sm:right-0 sm:mt-2 sm:w-80 dark:border-stone-800 dark:bg-stone-900"
      >
        {open &&
          (count === 0 ? (
            <Empty>Aucune notification.</Empty>
          ) : (
            <>
              <div className="mb-1 flex justify-end">
                <Button size="sm" variant="ghost" onClick={() => readAll.mutate()} disabled={readAll.isPending}>
                  Tout marquer comme lu
                </Button>
              </div>
              {(readAll.error ?? mark.error) && <Alert tone="error">{errorMessage(readAll.error ?? mark.error)}</Alert>}
              <ul className="flex max-h-96 flex-col gap-1 overflow-y-auto">
                {notifications.data?.map((n) => {
                  const digest = isDigest(n);
                  const shown = expanded === n.id;
                  return (
                    <li key={n.id} className="rounded p-2 hover:bg-stone-50 dark:hover:bg-stone-800">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium">
                            {n.title} {n.category === "critical" && <Badge tone="red">Critique</Badge>}
                          </p>
                          <p className="text-xs text-stone-600 dark:text-stone-400">{digest ? digestSummary(n) : n.body}</p>
                          <p className="text-xs text-muted">{formatDateTime(n.deliver_after)}</p>
                          {digest && (
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-expanded={shown}
                              onClick={() => setExpanded(shown ? null : n.id)}
                            >
                              {shown ? "Masquer le détail" : "Voir le détail"}
                            </Button>
                          )}
                        </div>
                        <div className="flex shrink-0 flex-col gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Marquer « ${n.title} » comme lu`}
                            onClick={() => mark.mutate({ id: n.id, status: "read" })}
                          >
                            Lu
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Ignorer « ${n.title} »`}
                            onClick={() => mark.mutate({ id: n.id, status: "dismissed" })}
                          >
                            Ignorer
                          </Button>
                        </div>
                      </div>
                      {digest && shown && <DigestItems id={n.id} />}
                    </li>
                  );
                })}
              </ul>
            </>
          ))}
      </div>
    </div>
  );
}
