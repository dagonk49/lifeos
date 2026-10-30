"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useMe } from "@/components/AppShell";
import { FinanceCategories } from "@/components/FinanceCategories";
import { FinanceImport, ImportHistory } from "@/components/FinanceImport";
import { FinanceProjection } from "@/components/FinanceProjection";
import { FinanceSummary } from "@/components/FinanceSummary";
import { ReceiptImport } from "@/components/ReceiptImport";
import { RecurringList } from "@/components/RecurringList";
import { TransactionForm } from "@/components/TransactionForm";
import { TransactionList } from "@/components/TransactionList";
import { Alert, Button, Card, Empty, PageHeader, Select, Spinner } from "@/components/ui";
import { monthLabel, monthOf, ofMonth, shiftMonth, todayIn } from "@/lib/finance";
import { useSpace } from "@/lib/space";
import type { Transaction } from "@/lib/types";

function FinancesView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const me = useMe();
  const { spaces, selected } = useSpace();

  // The owner's day, as the server counts it (a transaction date is a local calendar date).
  const today = todayIn(me.data?.user.timezone);
  const currentMonth = monthOf(today);
  const [chosenMonth, setChosenMonth] = useState<string | null>(null);
  const month = chosenMonth ?? currentMonth;
  // The budget and the categories belong to one space: the selected one, else a chosen one.
  const [pickedSpace, setPickedSpace] = useState("");
  const budgetSpace = selected ?? (spaces.length === 1 ? (spaces[0]?.id ?? null) : pickedSpace || null);

  const receiptId = searchParams.get("receipt");
  const importId = searchParams.get("import");
  const [importing, setImporting] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [creating, setCreating] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const setParam = (name: string, id: string | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (id) params.set(name, id);
    else params.delete(name);
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };
  const setReceiptParam = (id: string | null) => setParam("receipt", id);
  const closeStatement = () => {
    setImporting(false);
    if (importId) setParam("import", null);
  };
  const closeImport = () => {
    setScanning(false);
    if (receiptId) setReceiptParam(null);
  };
  const showTransaction = (tx: Transaction) => {
    const target = monthOf(tx.tx_date);
    setChosenMonth(target === currentMonth ? null : target);
    setHighlight(tx.id);
  };
  const recorded = (tx: Transaction, message: string) => {
    setNotice(message);
    showTransaction(tx);
  };
  const goToMonth = (next: string | null) => {
    setChosenMonth(next);
    setHighlight(null);
  };

  return (
    <>
      <PageHeader title="Finances">
        {!importing && !importId && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setNotice(null);
              setImporting(true);
            }}
          >
            Importer un relevé (CSV)
          </Button>
        )}
        {!scanning && !receiptId && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setNotice(null);
              setCreating(false);
              setScanning(true);
            }}
          >
            Scanner un ticket
          </Button>
        )}
        {!creating && (
          <Button
            size="sm"
            onClick={() => {
              setNotice(null);
              setCreating(true);
            }}
          >
            Nouvelle opération
          </Button>
        )}
      </PageHeader>
      <p className="-mt-2 mb-4 text-sm text-stone-600 dark:text-stone-400">
        Calculé localement, au centime près. Aucune connexion bancaire : vous saisissez vos opérations, scannez vos
        tickets ou importez le relevé CSV exporté depuis votre banque, et LifeOS n&apos;effectue jamais aucun paiement.
      </p>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <nav aria-label="Choix du mois" className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => goToMonth(shiftMonth(month, -1))}>
            <span aria-hidden>‹</span> Mois précédent
          </Button>
          <h2 className="min-w-32 text-center text-base font-semibold" aria-live="polite">
            {monthLabel(month)}
          </h2>
          <Button
            size="sm"
            variant="secondary"
            disabled={month >= currentMonth}
            onClick={() => {
              const next = shiftMonth(month, 1);
              goToMonth(next >= currentMonth ? null : next);
            }}
          >
            Mois suivant <span aria-hidden>›</span>
          </Button>
          {month !== currentMonth && (
            <Button size="sm" variant="ghost" onClick={() => goToMonth(null)}>
              Revenir au mois en cours
            </Button>
          )}
        </nav>
        {!selected && spaces.length > 1 && (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-stone-600 dark:text-stone-400">Espace du budget</span>
            <Select className="w-auto py-1" value={pickedSpace} onChange={(e) => setPickedSpace(e.target.value)}>
              <option value="">Choisir…</option>
              {spaces.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </label>
        )}
      </div>

      {notice && (
        <div className="mb-4">
          <Alert tone="success">{notice}</Alert>
        </div>
      )}

      {(importing || importId) && (
        <FinanceImport
          key={importId ?? "new"}
          importId={importId}
          defaultSpaceId={budgetSpace}
          onCreated={(id) => setParam("import", id)}
          onClose={closeStatement}
        />
      )}

      {(scanning || receiptId) && (
        <ReceiptImport
          key={receiptId ?? "scan"}
          documentId={receiptId}
          defaultSpaceId={budgetSpace}
          today={today}
          onUploaded={(id) => setReceiptParam(id)}
          onRecorded={(tx) => {
            closeImport();
            recorded(tx, "Opération ajoutée au budget à partir du ticket.");
          }}
          onShowTransaction={showTransaction}
          onClose={closeImport}
        />
      )}

      {creating && (
        <Card title="Nouvelle opération" className="mb-4">
          <TransactionForm
            defaultSpaceId={budgetSpace}
            today={today}
            onSaved={(tx) => {
              setCreating(false);
              recorded(tx, "Opération ajoutée.");
            }}
            onCancel={() => setCreating(false)}
          />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[auto_1fr] lg:items-start">
        <div className="lg:col-start-2 lg:row-start-1">
          {budgetSpace ? (
            <FinanceSummary spaceId={budgetSpace} month={month} />
          ) : (
            <Card title={`Budget ${ofMonth(month)}`}>
              <Empty>
                Le budget et les catégories se gèrent par espace : choisissez l&apos;espace du budget ci-dessus.
              </Empty>
            </Card>
          )}
        </div>
        <div className="lg:col-start-1 lg:row-span-2 lg:row-start-1">
          <TransactionList key={selected ?? "all"} month={month} today={today} highlightId={highlight} />
        </div>
        {budgetSpace && (
          <div className="lg:col-start-2 lg:row-start-2">
            <FinanceCategories key={budgetSpace} spaceId={budgetSpace} />
          </div>
        )}
      </div>

      {budgetSpace && (
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start">
          <FinanceProjection key={budgetSpace} spaceId={budgetSpace} />
          <div className="flex flex-col gap-4">
            <RecurringList key={budgetSpace} spaceId={budgetSpace} today={today} />
            <ImportHistory spaceId={budgetSpace} onOpen={(id) => setParam("import", id)} />
          </div>
        </div>
      )}
    </>
  );
}

export default function FinancesPage() {
  // useSearchParams needs a Suspense boundary for the static build.
  return (
    <Suspense fallback={<Spinner />}>
      <FinancesView />
    </Suspense>
  );
}
