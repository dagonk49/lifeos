"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { UsageSwitch } from "@/components/UsageSwitch";
import { Alert, Button, Card, Field, Input, Spinner } from "@/components/ui";
import { api, errorMessage, withReauth } from "@/lib/api";
import {
  CATEGORY_LABELS,
  MAX_DIGEST_TIMES,
  MAX_WINDOWS,
  parseMaxPerDay,
  preferenceErrors,
} from "@/lib/notifications";
import { loginWithPasskey } from "@/lib/passkeys";
import type {
  NotificationCategoryPreference,
  NotificationPreferences as Preferences,
  Settings,
} from "@/lib/types";

type Draft = NotificationCategoryPreference & { maxPerDay: string };

function toDraft(pref: NotificationCategoryPreference): Draft {
  return { ...pref, maxPerDay: pref.max_per_day === null ? "" : String(pref.max_per_day) };
}

function CriticalBypass({ enabled }: { enabled: boolean }) {
  const queryClient = useQueryClient();
  const descriptionId = useId();
  const toggle = useMutation({
    // Granting asks for a passkey confirmation; withdrawing is immediate.
    mutationFn: (next: boolean) =>
      withReauth(
        () => api<Settings>("/v1/settings", { method: "PATCH", body: { critical_bypass_quiet_hours: next } }),
        loginWithPasskey,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notification-preferences"] });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
  return (
    <div className="mb-4 rounded-md border border-stone-200 p-3 dark:border-stone-800">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Alertes critiques pendant la tranquillité</p>
          <p id={descriptionId} className="text-xs text-stone-600 dark:text-stone-400">
            Désactivé par défaut : les alertes critiques attendent alors la fin des plages de
            tranquillité, comme les autres. L&apos;activer demande une confirmation par passkey ; le
            retirer est immédiat. Le mode « Ne pas déranger » de votre téléphone reste prioritaire.
          </p>
        </div>
        <UsageSwitch
          checked={enabled}
          disabled={toggle.isPending}
          label="Laisser passer les alertes critiques pendant la tranquillité"
          describedBy={descriptionId}
          tone="amber"
          onChange={(next) => toggle.mutate(next)}
        />
      </div>
      {toggle.error && <Alert tone="error">{errorMessage(toggle.error)}</Alert>}
    </div>
  );
}

function TimeList({
  label,
  values,
  max,
  onChange,
}: {
  label: string;
  values: string[];
  max: number;
  onChange: (values: string[]) => void;
}) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-stone-700 dark:text-stone-300">{label}</span>
      <ul className="flex flex-wrap gap-2">
        {values.map((value, i) => (
          <li key={i} className="flex items-center gap-1">
            <Input
              type="time"
              aria-label={`${label} ${i + 1}`}
              value={value}
              onChange={(e) => onChange(values.map((v, j) => (j === i ? e.target.value : v)))}
              className="w-28"
            />
            <Button size="sm" variant="ghost" onClick={() => onChange(values.filter((_, j) => j !== i))}>
              Retirer
            </Button>
          </li>
        ))}
      </ul>
      {values.length < max && (
        <div>
          <Button size="sm" variant="secondary" onClick={() => onChange([...values, "18:00"])}>
            Ajouter une heure
          </Button>
        </div>
      )}
    </div>
  );
}

function CategoryForm({ draft, onChange }: { draft: Draft; onChange: (draft: Draft) => void }) {
  const titleId = useId();
  const important = draft.category === "important";
  return (
    <div
      role="group"
      aria-labelledby={titleId}
      className="flex flex-col gap-3 rounded-md border border-stone-200 p-3 dark:border-stone-800"
    >
      <div className="flex items-center justify-between gap-3">
        <h3 id={titleId} className="text-sm font-medium">
          {CATEGORY_LABELS[draft.category]}
        </h3>
        <UsageSwitch
          checked={draft.enabled}
          labelledBy={titleId}
          onChange={(enabled) => onChange({ ...draft, enabled })}
        />
      </div>
      {!draft.enabled && (
        <p className="text-xs text-amber-800 dark:text-amber-300">
          {important
            ? "Désactivées : aucun rappel de tâche ne vous sera notifié, même plus tard."
            : "Désactivées : aucune suggestion ne vous sera notifiée."}
        </p>
      )}
      <Field
        label="Maximum par jour"
        hint={important ? "Vide : sans limite. Au-delà, la suite attend le lendemain." : "Au-delà, la suite attend le regroupement du lendemain."}
      >
        <Input
          type="number"
          min={1}
          max={100}
          value={draft.maxPerDay}
          onChange={(e) => onChange({ ...draft, maxPerDay: e.target.value })}
          className="w-28"
        />
      </Field>
      {important ? (
        <div className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-stone-700 dark:text-stone-300">Créneaux de livraison</span>
          <span className="text-xs text-stone-500">
            Aucun créneau : immédiat, hors plages de tranquillité. Sinon, au prochain créneau.
          </span>
          <ul className="flex flex-col gap-2">
            {draft.windows.map((w, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <Input
                  type="time"
                  aria-label={`Créneau ${i + 1} : début`}
                  value={w.start}
                  onChange={(e) =>
                    onChange({ ...draft, windows: draft.windows.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)) })
                  }
                  className="w-28"
                />
                <span aria-hidden>→</span>
                <Input
                  type="time"
                  aria-label={`Créneau ${i + 1} : fin`}
                  value={w.end}
                  onChange={(e) =>
                    onChange({ ...draft, windows: draft.windows.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)) })
                  }
                  className="w-28"
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onChange({ ...draft, windows: draft.windows.filter((_, j) => j !== i) })}
                >
                  Retirer
                </Button>
              </li>
            ))}
          </ul>
          {draft.windows.length < MAX_WINDOWS && (
            <div>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => onChange({ ...draft, windows: [...draft.windows, { start: "09:00", end: "12:00" }] })}
              >
                Ajouter un créneau
              </Button>
            </div>
          )}
        </div>
      ) : (
        <TimeList
          label="Heure de regroupement"
          values={draft.digest_times}
          max={MAX_DIGEST_TIMES}
          onChange={(digest_times) => onChange({ ...draft, digest_times })}
        />
      )}
    </div>
  );
}

export function NotificationPreferences({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const prefs = useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => api<Preferences>("/v1/notifications/preferences"),
  });
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (prefs.data) setDrafts(prefs.data.categories.map(toDraft));
  }, [prefs.data]);
  const save = useMutation({
    mutationFn: () =>
      api<Preferences>("/v1/notifications/preferences", {
        method: "PUT",
        body: {
          categories: drafts.map((d) => ({
            category: d.category,
            enabled: d.enabled,
            max_per_day: parseMaxPerDay(d.maxPerDay) ?? null,
            ...(d.category === "important" ? { windows: d.windows } : { digest_times: d.digest_times }),
          })),
        },
      }),
    onSuccess: (data) => {
      setSaved(true);
      queryClient.setQueryData(["notification-preferences"], data);
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  const update = (index: number) => (draft: Draft) => {
    setSaved(false);
    setErrors([]);
    setDrafts((all) => all.map((d, i) => (i === index ? draft : d)));
  };

  return (
    <Card title="Notifications" className={className}>
      <p className="mb-3 text-sm text-stone-600 dark:text-stone-400">
        Les plages de tranquillité se règlent dans les préférences. Les suggestions arrivent
        regroupées en une seule notification, aux heures choisies ; les doublons ne sont jamais
        renvoyés.
      </p>
      {prefs.isPending ? (
        <Spinner />
      ) : prefs.error ? (
        <Alert tone="error">{errorMessage(prefs.error)}</Alert>
      ) : (
        <>
          <CriticalBypass enabled={prefs.data.critical_bypass_quiet_hours} />
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              const found = drafts.flatMap((d) => preferenceErrors(d, d.maxPerDay));
              setErrors(found);
              if (found.length === 0) save.mutate();
            }}
          >
            {drafts.map((draft, i) => (
              <CategoryForm key={draft.category} draft={draft} onChange={update(i)} />
            ))}
            {errors.length > 0 && (
              <Alert tone="error">
                <ul>
                  {errors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </Alert>
            )}
            {save.error && <Alert tone="error">{errorMessage(save.error)}</Alert>}
            <div className="flex items-center gap-2">
              <Button type="submit" disabled={save.isPending}>
                Enregistrer les notifications
              </Button>
              {saved && (
                <span role="status" className="text-sm text-teal-700">
                  Enregistré
                </span>
              )}
            </div>
          </form>
        </>
      )}
    </Card>
  );
}
