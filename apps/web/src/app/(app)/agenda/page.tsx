"use client";

import { useState } from "react";

import { AgendaDays } from "@/components/AgendaWeek";
import { NewEventForm } from "@/components/CalendarChange";
import { ConflictsList, SlotFinder } from "@/components/Planning";
import { Button, Card, PageHeader } from "@/components/ui";
import { addDays, mondayOf, weekDays, weekLabel } from "@/lib/calendar";
import { todayLocal } from "@/lib/format";
import { useSpace } from "@/lib/space";

export default function AgendaPage() {
  const { selected, writeSpaceId, spaceName } = useSpace();
  const [monday, setMonday] = useState(() => mondayOf(todayLocal()));
  const days = weekDays(monday);
  return (
    <>
      <PageHeader title="Agenda">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => setMonday(addDays(monday, -7))}>
            Semaine précédente
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setMonday(mondayOf(todayLocal()))}>
            Cette semaine
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setMonday(addDays(monday, 7))}>
            Semaine suivante
          </Button>
        </div>
      </PageHeader>
      <Card
        title={weekLabel(monday)}
        footer="Synchronisés toutes les 15 minutes environ, heures dans votre fuseau. « Déplacer » et « Annuler » ne modifient rien sans votre approbation."
      >
        <AgendaDays days={days} spaceId={selected} emptyText="Aucun événement cette semaine." editable />
      </Card>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Card
          title={`Conflits · ${spaceName(writeSpaceId)}`}
          footer="Calculés sur votre serveur, sans IA. Les événements des autres espaces restent anonymes."
        >
          <ConflictsList spaceId={writeSpaceId} />
        </Card>
        <Card
          title="Trouver un créneau"
          footer="« Bloquer ce créneau » propose l'événement : il n'est ajouté qu'après votre approbation."
        >
          <SlotFinder spaceId={writeSpaceId} />
        </Card>
        <Card
          title={`Nouvel événement · ${spaceName(writeSpaceId)}`}
          footer="Vous voyez l'aperçu exact avant toute écriture. Avec des participants, votre passkey est demandée."
        >
          <NewEventForm spaceId={writeSpaceId} today={todayLocal()} />
        </Card>
      </div>
    </>
  );
}
