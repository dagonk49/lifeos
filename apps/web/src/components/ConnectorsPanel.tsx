"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Alert, Badge, Button, Card, Empty, Field, Select, Spinner } from "@/components/ui";
import { api, errorMessage, withReauth } from "@/lib/api";
import {
  ACCOUNTS_KEY,
  CAPABILITIES,
  CAPABILITY_LABELS,
  PROVIDERS_KEY,
  STATUS_LABELS,
  STATUS_TONES,
  capabilityList,
  grantsIn,
  needsConsent,
  providerLabel,
  reconnectCapabilities,
  revocationAdvice,
  toggleCapability,
} from "@/lib/connectors";
import { formatDateTime } from "@/lib/format";
import { loginWithPasskey } from "@/lib/passkeys";
import { useSpace } from "@/lib/space";
import type {
  ConnectorAccount,
  ConnectorCapability,
  ConnectorProvider,
  DisconnectResult,
  OAuthStart,
  Space,
} from "@/lib/types";

type Navigate = (url: string) => void;

interface Notice {
  tone: "success" | "warning";
  text: string;
}

/** Leaves for the provider's consent page, which sends the browser back to /connexions/retour. */
function leaveForProvider(url: string) {
  window.location.assign(url);
}

/** Starts a consent (reinforced: a passkey confirmation if the last one is too old). */
function startConsent(body: {
  provider: string;
  space_id: string;
  capabilities: ConnectorCapability[];
  account_id?: string;
  reconnect?: boolean;
}): Promise<OAuthStart> {
  return withReauth(
    () => api<OAuthStart>("/v1/connectors/oauth/start", { method: "POST", body }),
    loginWithPasskey,
  );
}

function CapabilityBox({
  capability,
  checked,
  disabled,
  note,
  onChange,
}: {
  capability: ConnectorCapability;
  checked: boolean;
  disabled?: boolean;
  note?: string;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex min-h-8 items-center gap-2 text-sm">
      <input
        type="checkbox"
        className="size-4 accent-teal-700"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        {CAPABILITY_LABELS[capability]}
        {note && (
          <>
            {" "}
            <span className="text-xs text-stone-500">({note})</span>
          </>
        )}
      </span>
    </label>
  );
}

/** What one account may do in one space: ticking adds a consent, unticking withdraws it. */
function SpaceAccess({
  account,
  space,
  label,
  navigate,
}: {
  account: ConnectorAccount;
  space: Space;
  label: string;
  navigate: Navigate;
}) {
  const queryClient = useQueryClient();
  const granted = grantsIn(account, space.id).map((g) => g.capability);
  const active = account.status === "active";
  const change = useMutation({
    mutationFn: async ({
      capability,
      on,
    }: {
      capability: ConnectorCapability;
      on: boolean;
    }): Promise<{ redirect: string } | { account: ConnectorAccount }> => {
      const next = toggleCapability(granted, capability, on);
      // Not granted by the provider yet: a new consent on its page (incremental), which
      // adds the consent of this space on return.
      const consent = on ? needsConsent(account, next) : [];
      if (consent.length > 0) {
        const start = await startConsent({
          provider: account.provider,
          space_id: space.id,
          capabilities: consent,
          account_id: account.id,
        });
        return { redirect: start.authorization_url };
      }
      // Adding is reinforced (passkey); withdrawing is immediate.
      const updated = await withReauth(
        () =>
          api<ConnectorAccount>(`/v1/connectors/accounts/${encodeURIComponent(account.id)}/grants`, {
            method: "PUT",
            body: { space_id: space.id, capabilities: next },
          }),
        loginWithPasskey,
      );
      return { account: updated };
    },
    onSuccess: (result) => {
      if ("redirect" in result) {
        navigate(result.redirect);
        return;
      }
      queryClient.setQueryData<ConnectorAccount[]>(ACCOUNTS_KEY, (old) =>
        old?.map((a) => (a.id === result.account.id ? result.account : a)),
      );
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  return (
    <fieldset className="mt-2 rounded-md border border-stone-200 px-3 py-2 dark:border-stone-800">
      <legend className="px-1 text-sm font-medium">Espace « {space.name} »</legend>
      <div className="flex flex-wrap gap-x-5">
        {CAPABILITIES.map((capability) => {
          const checked = granted.includes(capability);
          return (
            <CapabilityBox
              key={capability}
              capability={capability}
              checked={checked}
              // A suspended account keeps its consents until withdrawn, but gets no new one.
              disabled={change.isPending || (!active && !checked)}
              note={!checked && active && !account.capabilities.includes(capability) ? `accord chez ${label}` : undefined}
              onChange={(on) => change.mutate({ capability, on })}
            />
          );
        })}
      </div>
      {granted.length === 0 && !change.isPending && (
        <p className="text-xs text-stone-500">Aucun usage dans cet espace.</p>
      )}
      {change.isPending && <Spinner label="Mise à jour des accès…" />}
      {change.error && <Alert tone="error">{errorMessage(change.error)}</Alert>}
    </fieldset>
  );
}

function DisconnectConfirm({
  account,
  name,
  label,
  onCancel,
  onDone,
}: {
  account: ConnectorAccount;
  name: string;
  label: string;
  onCancel: () => void;
  onDone: (result: DisconnectResult) => void;
}) {
  const titleId = useId();
  const purgeName = useId();
  const revoked = account.status === "revoked";
  // Reconnection needed: the tokens are already wiped, LifeOS has nothing to revoke with.
  const suspended = account.status === "reauth_required";
  // Asked every time; keeping the synced data is the default. A disconnected account has
  // nothing left but its data: deleting it is the only action left.
  const [purge, setPurge] = useState(revoked);
  const disconnect = useMutation({
    mutationFn: () =>
      api<DisconnectResult>(
        `/v1/connectors/accounts/${encodeURIComponent(account.id)}?purge=${purge ? "true" : "false"}`,
        { method: "DELETE" },
      ),
    onSuccess: onDone,
  });
  const action = revoked ? `Supprimer ${name}` : `Déconnecter ${name}`;

  return (
    <div
      role="group"
      aria-labelledby={titleId}
      className="mt-3 rounded-md border border-red-200 bg-red-50/60 p-3 dark:border-red-900 dark:bg-red-950/30"
    >
      <p id={titleId} className="text-sm font-medium">
        {action} ?
      </p>
      {revoked ? (
        <p className="mt-1 text-xs text-stone-700 dark:text-stone-300">
          Le compte et les données synchronisées depuis ce compte sont supprimés définitivement.
        </p>
      ) : (
        <>
          {suspended ? (
            <p className="mt-1 text-xs text-stone-700 dark:text-stone-300">
              LifeOS n&apos;a plus de jeton pour ce compte et ne peut donc pas révoquer son accès chez {label} : retirez
              LifeOS des applications autorisées dans les paramètres de sécurité du compte. LifeOS retire les
              autorisations de ce compte dans tous les espaces et annule les approbations en attente qui l&apos;utilisent.
            </p>
          ) : (
            <p className="mt-1 text-xs text-stone-700 dark:text-stone-300">
              LifeOS révoque son accès chez {label}, efface les jetons, retire les autorisations de ce compte dans tous
              les espaces et annule les approbations en attente qui l&apos;utilisent.
            </p>
          )}
          <fieldset className="mt-2 text-sm">
            <legend className="font-medium">Données déjà synchronisées depuis ce compte</legend>
            <label className="mt-1 flex min-h-8 items-center gap-2">
              <input
                type="radio"
                name={purgeName}
                className="size-4 accent-teal-700"
                checked={!purge}
                onChange={() => setPurge(false)}
                autoFocus
              />
              Les garder (consultables, plus mises à jour)
            </label>
            <label className="flex min-h-8 items-center gap-2">
              <input
                type="radio"
                name={purgeName}
                className="size-4 accent-red-700"
                checked={purge}
                onChange={() => setPurge(true)}
              />
              Les supprimer aussi (définitif)
            </label>
          </fieldset>
        </>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="danger" size="sm" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
          {action}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={disconnect.isPending}>
          Annuler
        </Button>
      </div>
      {disconnect.isPending && <Spinner label={revoked ? "Suppression…" : "Déconnexion…"} />}
      {disconnect.error && <Alert tone="error">{errorMessage(disconnect.error)}</Alert>}
    </div>
  );
}

function AccountItem({
  account,
  providers,
  navigate,
  onNotice,
}: {
  account: ConnectorAccount;
  providers: ConnectorProvider[] | undefined;
  navigate: Navigate;
  onNotice: (notice: Notice) => void;
}) {
  const queryClient = useQueryClient();
  const { spaces, spaceName, writeSpaceId } = useSpace();
  const [confirming, setConfirming] = useState(false);
  const triggerId = useId();
  const label = providerLabel(providers, account.provider);
  const name = account.email ?? `le compte ${label}`;
  const reconnect = useMutation({
    mutationFn: () =>
      // Tokens only: the consents per space stay as they are (the space is the context of
      // the passkey confirmation, nothing is added to it).
      startConsent({
        provider: account.provider,
        space_id: writeSpaceId || (spaces[0]?.id ?? ""),
        capabilities: reconnectCapabilities(account),
        account_id: account.id,
        reconnect: true,
      }),
    onSuccess: (start) => navigate(start.authorization_url),
  });

  const done = (result: DisconnectResult) => {
    setConfirming(false);
    const advice = revocationAdvice(result, label);
    const text =
      account.status === "revoked"
        ? `${name} est supprimé, avec les données synchronisées depuis ce compte.`
        : result.purged
          ? `${name} est déconnecté ; les données synchronisées depuis ce compte sont supprimées.`
          : `${name} est déconnecté. Les données déjà synchronisées sont conservées.`;
    onNotice({ tone: advice ? "warning" : "success", text: advice ? `${text} ${advice}` : text });
    void queryClient.invalidateQueries({ queryKey: ACCOUNTS_KEY });
    void queryClient.invalidateQueries({ queryKey: ["audit"] });
  };

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-medium break-all">{account.email ?? `Compte ${label}`}</span>
            <Badge tone={STATUS_TONES[account.status]}>{STATUS_LABELS[account.status]}</Badge>
          </p>
          <p className="text-xs text-stone-500">
            {label} ·{" "}
            {account.status === "revoked"
              ? `déconnecté le ${formatDateTime(account.revoked_at)}`
              : `connecté le ${formatDateTime(account.connected_at)}`}
          </p>
        </div>
        <Button
          id={triggerId}
          size="sm"
          variant="secondary"
          aria-expanded={confirming}
          onClick={() => setConfirming((open) => !open)}
        >
          {account.status === "revoked" ? "Supprimer…" : "Déconnecter…"}
        </Button>
      </div>

      {account.status === "reauth_required" && (
        <div className="mt-2">
          <Alert tone="warning">
            LifeOS n&apos;a plus accès à ce compte (accès retiré chez {label}, mot de passe changé ou autorisation
            expirée). Ses usages sont suspendus jusqu&apos;à la reconnexion, qui conserve ses autorisations par espace
            sans en ajouter.
          </Alert>
          <Button className="mt-2" size="sm" onClick={() => reconnect.mutate()} disabled={reconnect.isPending}>
            Reconnecter le compte
          </Button>
          {reconnect.error && <Alert tone="error">{errorMessage(reconnect.error)}</Alert>}
        </div>
      )}
      {account.status === "active" && (
        <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">
          Accordé chez {label} : {account.capabilities.length ? capabilityList(account.capabilities) : "aucun usage"}.
        </p>
      )}
      {account.status === "revoked" ? (
        <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">
          Aucun accès : jetons effacés, autorisations retirées. Pour l&apos;utiliser à nouveau, connectez-le avec le
          formulaire ci-dessous.
        </p>
      ) : (
        spaces.map((space) => (
          <SpaceAccess key={space.id} account={account} space={space} label={label} navigate={navigate} />
        ))
      )}

      {account.sources.length > 0 && (
        <div className="mt-2">
          <h4 className="text-xs font-medium">Agendas et libellés suivis</h4>
          <ul className="text-xs text-stone-600 dark:text-stone-400">
            {account.sources.map((source) => (
              <li key={source.id}>
                {source.kind === "calendar" ? "Agenda" : "Libellé"} « {source.name} » · {spaceName(source.space_id)} ·{" "}
                {!source.enabled
                  ? "désactivé"
                  : source.last_synced_at
                    ? `synchronisé le ${formatDateTime(source.last_synced_at)}`
                    : "pas encore synchronisé"}
              </li>
            ))}
          </ul>
        </div>
      )}

      {confirming && (
        <DisconnectConfirm
          account={account}
          name={name}
          label={label}
          onCancel={() => {
            setConfirming(false);
            document.getElementById(triggerId)?.focus();
          }}
          onDone={done}
        />
      )}
    </li>
  );
}

function ConnectForm({ providers, navigate }: { providers: ConnectorProvider[]; navigate: Navigate }) {
  const { spaces, writeSpaceId } = useSpace();
  const [provider, setProvider] = useState(providers[0]?.name ?? "");
  const [spaceId, setSpaceId] = useState(writeSpaceId);
  // Least access by default: reading the calendar. The owner ticks what else is needed.
  const [capabilities, setCapabilities] = useState<ConnectorCapability[]>(["calendar.read"]);
  const [missingChoice, setMissingChoice] = useState(false);
  const label = providerLabel(providers, provider);
  const connect = useMutation({
    mutationFn: () => startConsent({ provider, space_id: spaceId || (spaces[0]?.id ?? ""), capabilities }),
    onSuccess: (start) => navigate(start.authorization_url),
  });

  return (
    <form
      className="mt-4 border-t border-stone-200 pt-4 dark:border-stone-800"
      onSubmit={(e) => {
        e.preventDefault();
        if (capabilities.length === 0) {
          setMissingChoice(true);
          return;
        }
        connect.mutate();
      }}
    >
      <h3 className="text-sm font-semibold">Connecter un compte</h3>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        {providers.length > 1 && (
          <Field label="Fournisseur">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {providers.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.label}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Field label="Espace" hint="Vous pourrez ensuite l'autoriser dans d'autres espaces.">
          <Select value={spaceId} onChange={(e) => setSpaceId(e.target.value)}>
            {spaces.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <fieldset className="mt-3">
        <legend className="text-sm font-medium">Usages autorisés dans cet espace</legend>
        <div className="flex flex-wrap gap-x-5">
          {CAPABILITIES.map((capability) => (
            <CapabilityBox
              key={capability}
              capability={capability}
              checked={capabilities.includes(capability)}
              onChange={(on) => {
                setMissingChoice(false);
                setCapabilities((current) => toggleCapability(current, capability, on));
              }}
            />
          ))}
        </div>
      </fieldset>
      <p className="mt-1 text-xs text-stone-500">
        Vous serez redirigé vers {label} pour accepter ces accès. Ce que vous y décochez reste coupé : LifeOS
        n&apos;utilise que ce qui est réellement accordé.
      </p>
      {missingChoice && <Alert tone="error">Cochez au moins un usage à autoriser.</Alert>}
      <Button type="submit" className="mt-3" disabled={connect.isPending}>
        Connecter un compte {label}
      </Button>
      {connect.isPending && <Spinner label={`Ouverture de ${label}…`} />}
      {connect.error && <Alert tone="error">{errorMessage(connect.error)}</Alert>}
    </form>
  );
}

/**
 * Connected accounts (ADR 0012): which external sources LifeOS may use, in which space and
 * for what. Connecting or adding a use is reinforced (passkey); withdrawing is immediate.
 */
export function ConnectorsPanel({
  className,
  navigate = leaveForProvider,
}: {
  className?: string;
  /** Opens the provider's consent page (replaced in tests). */
  navigate?: Navigate;
}) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const providers = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: () => api<ConnectorProvider[]>("/v1/connectors/providers"),
  });
  const accounts = useQuery({
    queryKey: ACCOUNTS_KEY,
    queryFn: () => api<ConnectorAccount[]>("/v1/connectors/accounts"),
  });

  return (
    <Card title="Comptes connectés" className={className}>
      <p className="mb-2 text-sm text-stone-600 dark:text-stone-400">
        Un compte connecté (agenda, e-mails) ne sert que dans les espaces et pour les usages cochés ici. Connecter un
        compte ou ajouter un usage demande une confirmation par passkey ; retirer un usage ou déconnecter est immédiat.
        Les jetons d&apos;accès restent chiffrés sur votre serveur et ne sont jamais affichés.
      </p>
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}
      {accounts.isPending ? (
        <Spinner />
      ) : accounts.error ? (
        <Alert tone="error">{errorMessage(accounts.error)}</Alert>
      ) : accounts.data.length === 0 ? (
        <Empty>Aucun compte connecté : LifeOS n&apos;accède à aucun agenda ni aucune boîte e-mail.</Empty>
      ) : (
        <ul className="divide-y divide-stone-100 dark:divide-stone-800">
          {accounts.data.map((account) => (
            <AccountItem
              key={account.id}
              account={account}
              providers={providers.data}
              navigate={navigate}
              onNotice={setNotice}
            />
          ))}
        </ul>
      )}
      {providers.data && providers.data.length > 0 ? (
        <ConnectForm providers={providers.data} navigate={navigate} />
      ) : providers.isSuccess ? (
        <p className="mt-3 text-xs text-stone-500">
          La connexion de comptes n&apos;est pas activée sur ce serveur : l&apos;administrateur l&apos;active en
          suivant le guide d&apos;installation (section « Comptes connectés »).
        </p>
      ) : providers.error ? (
        <Alert tone="error">{errorMessage(providers.error)}</Alert>
      ) : null}
    </Card>
  );
}
