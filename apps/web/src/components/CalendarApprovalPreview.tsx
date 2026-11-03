import { Badge } from "@/components/ui";
import type { CalendarPreview } from "@/lib/types";

export const CALENDAR_ACTIONS = new Set([
  "calendar.create_event",
  "calendar.move_event",
  "calendar.cancel_event",
  "calendar.update_shared_event",
]);

const OPERATION: Record<CalendarPreview["operation"], string> = {
  create: "Nouvel événement",
  move: "Déplacement",
  cancel: "Annulation",
};

/**
 * Exact preview of a calendar change, as the server read it from the provider: calendar,
 * before and after in the owner's zone, attendees and whether they are told. Event texts
 * come from third parties: shown as plain text, never interpreted.
 */
export function CalendarApprovalPreview({ preview }: { preview: CalendarPreview }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="violet">{OPERATION[preview.operation]}</Badge>
        {preview.shared && <Badge tone="amber">Événement partagé — confirmation par passkey</Badge>}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted">Événement</dt>
        <dd className="break-words font-medium">{preview.summary || "(sans titre)"}</dd>
        <dt className="text-muted">Agenda</dt>
        <dd className="break-words">{preview.calendar}</dd>
        {preview.before && (
          <>
            <dt className="text-muted">{preview.operation === "cancel" ? "Prévu" : "Avant"}</dt>
            <dd className={preview.operation === "cancel" ? "line-through" : undefined}>{preview.before}</dd>
          </>
        )}
        {preview.after && (
          <>
            <dt className="text-muted">{preview.operation === "create" ? "Quand" : "Après"}</dt>
            <dd className="font-medium">{preview.after}</dd>
          </>
        )}
        {preview.location && (
          <>
            <dt className="text-muted">Lieu</dt>
            <dd className="break-words">{preview.location}</dd>
          </>
        )}
        <dt className="text-muted">Participants</dt>
        <dd>
          {preview.attendees.length === 0 ? (
            "Aucun autre participant"
          ) : (
            <ul aria-label="Participants" className="flex flex-col">
              {preview.attendees.map((a) => (
                <li key={a} className="break-all">
                  {a}
                </li>
              ))}
            </ul>
          )}
        </dd>
        {preview.attendees.length > 0 && (
          <>
            <dt className="text-muted">Prévenir</dt>
            <dd>{preview.notify ? "Oui, par e-mail du fournisseur" : "Non, personne n'est prévenu"}</dd>
          </>
        )}
      </dl>
      {preview.notes.length > 0 && (
        <ul className="ml-4 list-disc text-xs text-stone-600 dark:text-stone-400">
          {preview.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
