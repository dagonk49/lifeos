"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { NotificationItem } from "@/lib/types";

export function NotificationsMenu() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<NotificationItem[]>("/v1/notifications"),
    refetchInterval: 60_000,
  });
  const mark = useMutation({
    mutationFn: (id: string) =>
      api(`/v1/notifications/${id}`, { method: "PATCH", body: { status: "read" } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const count = notifications.data?.length ?? 0;

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="sm"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
      >
        Notifications{count > 0 && <Badge tone="teal">{count}</Badge>}
      </Button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-stone-200 bg-white p-2 shadow-lg dark:border-stone-800 dark:bg-stone-900">
          {count === 0 ? (
            <Empty>Aucune notification.</Empty>
          ) : (
            <ul className="flex max-h-96 flex-col gap-1 overflow-y-auto">
              {notifications.data?.map((n) => (
                <li key={n.id} className="flex items-start justify-between gap-2 rounded p-2 hover:bg-stone-50 dark:hover:bg-stone-800">
                  <div>
                    <p className="text-sm font-medium">{n.title}</p>
                    {n.body && <p className="text-xs text-stone-600">{n.body}</p>}
                    <p className="text-xs text-stone-500">{formatDateTime(n.deliver_after)}</p>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => mark.mutate(n.id)}>
                    Lu
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
