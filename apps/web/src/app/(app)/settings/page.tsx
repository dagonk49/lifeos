"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { AgentAccessPanel } from "@/components/AgentAccessPanel";
import { ConnectorsPanel } from "@/components/ConnectorsPanel";
import { DataExport } from "@/components/DataExport";
import { NotificationPreferences } from "@/components/NotificationPreferences";
import { SessionList } from "@/components/SessionList";
import { UsagePanel } from "@/components/UsagePanel";
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";
import { api, errorMessage, withReauth } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { loginWithPasskey, registerPasskey } from "@/lib/passkeys";
import type {
  AgentPermission,
  AuditEvent,
  Passkey,
  Settings,
  Space,
} from "@/lib/types";

const AGENTS = [
  "core",
  "planning",
  "memory",
  "coaching",
  "finance",
  "projects",
  "documents",
];
// Mirror of guardian/models.py RESOURCES.
const RESOURCES = [
  "tasks",
  "journal",
  "facts",
  "memories",
  "messages",
  "goals",
  "reviews",
  "documents",
  "finances",
  "calendar",
  "email",
];

function useInvalidate(...keys: string[]) {
  const queryClient = useQueryClient();
  return () =>
    keys.forEach(
      (key) => void queryClient.invalidateQueries({ queryKey: [key] }),
    );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <Card title={title}>{children}</Card>;
}

function SpacesSection() {
  const invalidate = useInvalidate("spaces", "dashboard");
  const spaces = useQuery({
    queryKey: ["spaces"],
    queryFn: () => api<Space[]>("/v1/spaces"),
  });
  const toggle = useMutation({
    mutationFn: (space: Space) =>
      withReauth(
        () =>
          api<Space>(`/v1/spaces/${space.id}`, {
            method: "PATCH",
            body: { cloud_ai_enabled: !space.cloud_ai_enabled },
          }),
        loginWithPasskey,
      ),
    onSuccess: invalidate,
  });
  return (
    <Section title="Espaces et assistant IA">
      <p className="mb-2 text-sm text-stone-600 dark:text-stone-400">
        Autoriser l&apos;assistant pour un espace permet d&apos;envoyer au
        fournisseur IA des extraits minimaux de cet espace, après masquage local
        des données sensibles. Changer ce réglage demande une confirmation par
        passkey.
      </p>
      {spaces.isPending ? (
        <Spinner />
      ) : (
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {spaces.data?.map((space) => (
            <li
              key={space.id}
              className="flex items-center justify-between py-2"
            >
              <span>
                {space.name}{" "}
                <Badge tone={space.cloud_ai_enabled ? "teal" : "neutral"}>
                  {space.cloud_ai_enabled ? "IA autorisée" : "IA non autorisée"}
                </Badge>
              </span>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => toggle.mutate(space)}
                disabled={toggle.isPending}
              >
                {space.cloud_ai_enabled
                  ? "Retirer l'autorisation"
                  : "Autoriser l'IA"}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {toggle.error && <Alert tone="error">{errorMessage(toggle.error)}</Alert>}
    </Section>
  );
}

function PreferencesSection() {
  const invalidate = useInvalidate("settings", "dashboard", "llm-usage", "notifications");
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/v1/settings") });
  const [form, setForm] = useState({ display_name: "", timezone: "", start: "", end: "", budget: "" });
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (settings.data) {
      setForm({
        display_name: settings.data.display_name,
        timezone: settings.data.timezone,
        start: settings.data.quiet_hours_start?.slice(0, 5) ?? "",
        end: settings.data.quiet_hours_end?.slice(0, 5) ?? "",
        budget: String(settings.data.llm_monthly_token_budget),
      });
    }
  }, [settings.data]);
  const save = useMutation({
    mutationFn: () =>
      api<Settings>("/v1/settings", {
        method: "PATCH",
        body: {
          display_name: form.display_name,
          timezone: form.timezone,
          quiet_hours_start: form.start || null,
          quiet_hours_end: form.end || null,
          llm_monthly_token_budget: Number(form.budget),
        },
      }),
    onSuccess: () => {
      setSaved(true);
      invalidate();
    },
  });
  const set =
    (key: keyof typeof form) => (e: { target: { value: string } }) => {
      setSaved(false);
      setForm((f) => ({ ...f, [key]: e.target.value }));
    };
  return (
    <Section title="Préférences">
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field label="Nom affiché">
          <Input value={form.display_name} onChange={set("display_name")} />
        </Field>
        <Field label="Fuseau horaire" hint="Nom IANA, ex. Europe/Paris">
          <Input value={form.timezone} onChange={set("timezone")} />
        </Field>
        <Field label="Tranquillité : début" hint="Les notifications attendent la fin (alertes critiques : seulement si vous l'autorisez).">
          <Input type="time" value={form.start} onChange={set("start")} />
        </Field>
        <Field label="Tranquillité : fin">
          <Input type="time" value={form.end} onChange={set("end")} />
        </Field>
        <Field
          label="Budget IA mensuel (jetons)"
          hint="Arrêt automatique des appels au-delà."
        >
          <Input
            type="number"
            min={0}
            value={form.budget}
            onChange={set("budget")}
          />
        </Field>
        <div className="flex items-end gap-2">
          <Button type="submit" disabled={save.isPending}>
            Enregistrer
          </Button>
          {saved && (
            <span role="status" className="text-sm text-teal-700">
              Enregistré
            </span>
          )}
        </div>
      </form>
      {save.error && <Alert tone="error">{errorMessage(save.error)}</Alert>}
    </Section>
  );
}

function DevicesSection() {
  const invalidate = useInvalidate("passkeys", "sessions");
  const passkeys = useQuery({
    queryKey: ["passkeys"],
    queryFn: () => api<Passkey[]>("/v1/auth/passkeys"),
  });
  const [label, setLabel] = useState("");
  const revokePasskey = useMutation({
    mutationFn: (id: string) =>
      withReauth(
        () => api(`/v1/auth/passkeys/${id}`, { method: "DELETE" }),
        loginWithPasskey,
      ),
    onSuccess: invalidate,
  });
  const addPasskey = useMutation({
    mutationFn: () =>
      withReauth(
        () => registerPasskey(label || "Nouvel appareil"),
        loginWithPasskey,
      ),
    onSuccess: () => {
      setLabel("");
      invalidate();
    },
  });
  return (
    <Section title="Appareils et passkeys">
      <h3 className="text-sm font-medium">Passkeys</h3>
      <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
        {passkeys.data?.map((p) => (
          <li key={p.id} className="flex items-center justify-between py-2">
            <span>
              {p.label}{" "}
              {p.backup_state && <Badge tone="blue">Synchronisée</Badge>}
              <span className="block text-xs text-stone-500">
                Créée le {formatDateTime(p.created_at)}
                {p.last_used_at &&
                  ` · utilisée le ${formatDateTime(p.last_used_at)}`}
              </span>
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => revokePasskey.mutate(p.id)}
            >
              Révoquer
            </Button>
          </li>
        ))}
      </ul>
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          addPasskey.mutate();
        }}
      >
        <Input
          aria-label="Nom du nouvel appareil"
          placeholder="Nom de l'appareil"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <Button
          type="submit"
          variant="secondary"
          disabled={addPasskey.isPending}
        >
          Ajouter une passkey
        </Button>
      </form>
      {(revokePasskey.error || addPasskey.error) && (
        <Alert tone="error">
          {errorMessage(revokePasskey.error ?? addPasskey.error)}
        </Alert>
      )}

      <SessionList />
    </Section>
  );
}

function PermissionsSection() {
  // "agents": the per-space access shown in « Agents spécialisés ».
  const invalidate = useInvalidate("permissions", "agents");
  const spaces = useQuery({
    queryKey: ["spaces"],
    queryFn: () => api<Space[]>("/v1/spaces"),
  });
  const permissions = useQuery({
    queryKey: ["permissions"],
    queryFn: () => api<AgentPermission[]>("/v1/guardian/permissions"),
  });
  const [form, setForm] = useState({
    agent: "coaching",
    space_id: "",
    resource: "journal",
    access: "read",
    purpose: "",
    days: "30",
  });
  const grant = useMutation({
    mutationFn: () =>
      withReauth(
        () =>
          api<AgentPermission>("/v1/guardian/permissions", {
            method: "POST",
            body: {
              agent: form.agent,
              space_id: form.space_id || spaces.data?.[0]?.id,
              resource: form.resource,
              access: form.access,
              purpose: form.purpose,
              expires_at: form.days
                ? new Date(
                    Date.now() + Number(form.days) * 86_400_000,
                  ).toISOString()
                : null,
            },
          }),
        loginWithPasskey,
      ),
    onSuccess: () => {
      setForm((f) => ({ ...f, purpose: "" }));
      invalidate();
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      api(`/v1/guardian/permissions/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
  const spaceName = (id: string) =>
    spaces.data?.find((s) => s.id === id)?.name ?? id;
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  return (
    <Section title="Permissions des agents">
      <p className="mb-2 text-sm text-stone-600 dark:text-stone-400">
        Un agent spécialisé n&apos;accède qu&apos;aux données explicitement
        autorisées ici, pour une finalité et une durée données. Il ne peut
        jamais s&apos;octroyer de permission.
      </p>
      {permissions.data?.length === 0 ? (
        <Empty>Aucune permission accordée.</Empty>
      ) : (
        <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {permissions.data?.map((p) => (
            <li
              key={p.id}
              className="flex items-center justify-between gap-2 py-2"
            >
              <span>
                <strong>{p.agent}</strong> ·{" "}
                {p.access === "write" ? "lecture/écriture" : "lecture"}{" "}
                {p.resource} · {spaceName(p.space_id)}
                <span className="block text-xs text-stone-500">
                  {p.purpose}
                  {p.expires_at &&
                    ` · jusqu'au ${formatDateTime(p.expires_at)}`}
                </span>
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => revoke.mutate(p.id)}
              >
                Retirer
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="mt-3 grid gap-2 sm:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault();
          grant.mutate();
        }}
      >
        <Field label="Agent">
          <Select value={form.agent} onChange={set("agent")}>
            {AGENTS.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </Select>
        </Field>
        <Field label="Espace">
          <Select value={form.space_id} onChange={set("space_id")}>
            {spaces.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Données">
          <Select value={form.resource} onChange={set("resource")}>
            {RESOURCES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </Select>
        </Field>
        <Field label="Accès">
          <Select value={form.access} onChange={set("access")}>
            <option value="read">Lecture</option>
            <option value="write">Lecture et écriture</option>
          </Select>
        </Field>
        <Field label="Durée (jours)">
          <Input
            type="number"
            min={1}
            value={form.days}
            onChange={set("days")}
          />
        </Field>
        <Field label="Finalité">
          <Input
            value={form.purpose}
            onChange={set("purpose")}
            minLength={3}
            required
          />
        </Field>
        <div className="sm:col-span-3">
          <Button type="submit" variant="secondary" disabled={grant.isPending}>
            Accorder
          </Button>
        </div>
      </form>
      {grant.error && <Alert tone="error">{errorMessage(grant.error)}</Alert>}
    </Section>
  );
}

function AuditSection() {
  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: () => api<{ items: AuditEvent[] }>("/v1/guardian/audit?limit=30"),
  });
  return (
    <Section title="Journal d'audit">
      {audit.isPending ? (
        <Spinner />
      ) : (
        <ul className="max-h-80 divide-y divide-stone-100 overflow-y-auto text-xs dark:divide-stone-800">
          {audit.data?.items.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center gap-2 py-1.5">
              <span className="text-stone-500">
                {formatDateTime(e.occurred_at)}
              </span>
              <code>{e.action}</code>
              <Badge tone={e.outcome === "success" ? "neutral" : "red"}>
                {e.outcome}
              </Badge>
              <span className="text-stone-500">{e.actor_type}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Réglages" />
      <div className="grid gap-4 lg:grid-cols-2">
        <SpacesSection />
        <PreferencesSection />
        <NotificationPreferences className="lg:col-span-2" />
        {/* Anchor of the dashboard's "Réglages de l'assistant" link. */}
        <div id="assistant-ia" className="scroll-mt-4 lg:col-span-2">
          <UsagePanel />
        </div>
        <AgentAccessPanel className="lg:col-span-2" />
        {/* Anchor of the return page of a connection (/connexions/retour). */}
        <div id="connexions" className="scroll-mt-4 lg:col-span-2">
          <ConnectorsPanel />
        </div>
        <DevicesSection />
        <PermissionsSection />
        <AuditSection />
        {/* Anchor of the memory page's "Exporter mes données" link. */}
        <div id="mes-donnees" className="scroll-mt-4">
          <Section title="Mes données">
            <DataExport />
          </Section>
        </div>
      </div>
    </>
  );
}
