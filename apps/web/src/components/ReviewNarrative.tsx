import Link from "next/link";
import { useId, type ReactNode } from "react";

import { Alert, Badge } from "@/components/ui";
import { narrativeNotice } from "@/lib/reviews";
import type { Review } from "@/lib/types";

function Part({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="mt-3">
      <h4 id={id} className="text-sm font-semibold">
        {title}
      </h4>
      {intro && <p className="text-xs text-stone-600 dark:text-stone-400">{intro}</p>}
      {children}
    </section>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

/** Block b): the assistant's reading of the facts (an interpretation, never a fact). */
export function ReviewNarrativeBlock({ review }: { review: Review }) {
  const headingId = useId();
  const narrative = review.narrative;
  const notice = narrative ? null : narrativeNotice(review);
  const progress = narrative?.progress ?? [];
  const obstacles = narrative?.obstacles ?? [];
  const suggestions = narrative?.suggestions ?? [];
  const interpretations = narrative?.interpretations ?? [];
  const questions = narrative?.questions ?? [];

  return (
    <section
      aria-labelledby={headingId}
      className="rounded-lg border border-violet-200 bg-violet-50/50 p-3 sm:p-4 dark:border-violet-900 dark:bg-violet-950/20"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className="text-base font-semibold">
          Lecture de l&apos;assistant
        </h3>
        {narrative && (
          <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <Badge tone="violet">Rédigée par l&apos;IA</Badge>
            {review.model && <span>Modèle : {review.model}</span>}
          </span>
        )}
      </header>

      {narrative ? (
        <>
          <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">
            Rédigée à partir des faits ci-dessus et de quelques extraits de votre journal. Elle peut se
            tromper : en cas de doute, les faits font foi.
          </p>
          {narrative.summary && <p className="mt-3 text-sm leading-relaxed">{narrative.summary}</p>}

          {progress.length > 0 && (
            <Part title="Progrès">
              <Bullets items={progress} />
            </Part>
          )}
          {obstacles.length > 0 && (
            <Part title="Difficultés">
              <Bullets items={obstacles} />
            </Part>
          )}
          {suggestions.length > 0 && (
            <Part title="Suggestions">
              <ol className="mt-1 flex flex-col gap-2 text-sm">
                {suggestions.map((s, i) => (
                  <li key={i} className="rounded-md bg-white/70 p-2 dark:bg-stone-900/60">
                    <p className="font-medium">{s.action}</p>
                    {s.why && (
                      <p className="mt-0.5 text-xs text-stone-600 dark:text-stone-400">
                        <span className="font-medium">Pourquoi :</span> {s.why}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            </Part>
          )}
          {interpretations.length > 0 && (
            <Part
              title="Hypothèses — à confirmer ou infirmer"
              intro="Des pistes de lecture, pas des faits : vous seul savez si elles sont justes."
            >
              <ul className="mt-1 flex flex-col gap-1.5 text-sm">
                {interpretations.map((item, i) => (
                  <li key={i} className="rounded-md border border-dashed border-violet-300 px-2 py-1.5 italic dark:border-violet-800">
                    {item}
                  </li>
                ))}
              </ul>
            </Part>
          )}
          {questions.length > 0 && (
            <Part title="Questions pour réfléchir">
              <Bullets items={questions} />
            </Part>
          )}
        </>
      ) : (
        notice && (
          <div className="mt-2">
            <Alert tone={notice.tone}>
              {notice.text}
              {notice.settings && (
                <>
                  {" "}
                  <Link href="/settings" className="font-medium underline">
                    Ouvrir les réglages
                  </Link>
                </>
              )}
            </Alert>
          </div>
        )
      )}
    </section>
  );
}
