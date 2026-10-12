"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { NotificationsMenu } from "@/components/NotificationsMenu";
import { Button, Select, Spinner, cx } from "@/components/ui";
import { api, isApiError } from "@/lib/api";
import { SpaceProvider, useSpace } from "@/lib/space";
import type { Me } from "@/lib/types";

const NAV = [
  { href: "/", label: "Accueil" },
  { href: "/chat", label: "Assistant" },
  { href: "/tasks", label: "Tâches" },
  { href: "/journal", label: "Journal" },
  { href: "/goals", label: "Objectifs" },
  { href: "/documents", label: "Documents" },
  { href: "/finances", label: "Finances" },
  { href: "/reviews", label: "Bilans" },
  { href: "/memory", label: "Mémoire" },
  { href: "/search", label: "Recherche" },
  { href: "/approvals", label: "Approbations" },
  { href: "/settings", label: "Réglages" },
];

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/v1/auth/me") });
}

function SpaceSwitcher() {
  const { spaces, selected, setSelected } = useSpace();
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="sr-only">Espace</span>
      <Select
        value={selected ?? ""}
        onChange={(e) => setSelected(e.target.value || null)}
        className="w-auto py-1"
        aria-label="Espace"
      >
        <option value="">Tous les espaces</option>
        {spaces.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </Select>
    </label>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const me = useMe();
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    if (me.error && isApiError(me.error, "unauthenticated")) router.replace("/login");
  }, [me.error, router]);

  if (me.isPending || (me.error && isApiError(me.error, "unauthenticated"))) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (me.error || !me.data) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4 text-sm">
        Serveur LifeOS injoignable. Réessayez dans un instant.
      </div>
    );
  }

  async function logout() {
    setLoggingOut(true);
    try {
      await api("/v1/auth/logout", { method: "POST" });
    } finally {
      queryClient.clear();
      router.replace("/login");
    }
  }

  return (
    <SpaceProvider spaces={me.data.spaces}>
      <div className="min-h-screen">
        <header className="sticky top-0 z-20 border-b border-stone-200 bg-white/90 backdrop-blur dark:border-stone-800 dark:bg-stone-950/90">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-2">
            <Link href="/" className="font-semibold text-teal-800 dark:text-teal-300">
              LifeOS
            </Link>
            <div className="flex items-center gap-2">
              <SpaceSwitcher />
              <NotificationsMenu />
              <Button variant="ghost" size="sm" onClick={logout} disabled={loggingOut}>
                Déconnexion
              </Button>
            </div>
          </div>
          <nav aria-label="Navigation principale" className="mx-auto max-w-6xl overflow-x-auto px-2">
            <ul className="flex gap-1 pb-2">
              {NAV.map((item) => {
                const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={cx(
                        "block whitespace-nowrap rounded-md px-3 py-1.5 text-sm",
                        active
                          ? "bg-teal-100 font-medium text-teal-900 dark:bg-teal-900/50 dark:text-teal-100"
                          : "text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800",
                      )}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl p-4">{children}</main>
      </div>
    </SpaceProvider>
  );
}
