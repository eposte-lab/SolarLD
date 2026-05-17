/**
 * Follow-up template defaults — shared between the lead-detail
 * compositore (`follow-up-drafter.tsx`) and the Settings editor
 * (`settings/follow-up-templates`).
 *
 * `label`/`description` are fixed (they describe WHEN to use each
 * template). The operator can override `subject`/`body` per tenant;
 * the overrides live on `tenants.followup_templates` (migration 0134)
 * keyed by template `id`. `mergeFollowupTemplates` applies them.
 */

export interface FollowupTemplate {
  id: string;
  label: string;
  description: string;
  subject: string;
  body: string;
}

/** Per-tenant overrides keyed by template id. */
export type FollowupTemplateOverrides = Record<
  string,
  { subject?: string; body?: string } | undefined
>;

export const FOLLOWUP_TEMPLATE_DEFAULTS: FollowupTemplate[] = [
  {
    id: 'recap_roi',
    label: 'Recap ROI',
    description:
      'Per lead che hanno aperto il portale o cliccato la CTA. Riassume i numeri concreti e propone un sopralluogo.',
    subject: '{{azienda}} — i suoi numeri sul fotovoltaico',
    body: `Buongiorno {{nome}},

ho rivisto i dati che il sistema ha calcolato per {{azienda}} e volevo condividere i tre numeri che pensavamo di portarle al sopralluogo:

• Potenza ottimale: circa {{kwp}} kW
• Risparmio annuo stimato: {{risparmio}}
• Rientro investimento: {{payback}}

Sono numeri pensati sul tetto della sua sede a {{comune}} — non un preventivo generico. Le proporrei un sopralluogo gratuito (40 minuti circa) per validare i dati direttamente sul posto e poi metterle in mano un preventivo definitivo.

Mi farebbe sapere se la prossima settimana ha una mezz'oretta libera tra mercoledì e venerdì?

A presto,
{{firma}}`,
  },
  {
    id: 'reattivazione_fredda',
    label: 'Riattivazione lead freddo',
    description:
      "Lead che ha aperto la prima email ma non ha cliccato. Tono leggero, niente pressione, una sola domanda chiara.",
    subject: 'Una domanda veloce per {{azienda}}',
    body: `Buongiorno {{nome}},

mi rendo conto che il fotovoltaico non è in cima alla lista delle priorità di chi gestisce {{azienda}} — capita.

Le faccio solo una domanda secca: oggi quanto le costa l'energia in un anno medio? Se fosse anche solo {{risparmio_annuo_minimo}} all'anno, varrebbe la pena guardarci dentro per 30 minuti.

Le mando in allegato lo studio personalizzato sul suo tetto a {{comune}}. Se le interessa parlarne, basta che mi risponda anche solo "sì".

Cordialmente,
{{firma}}`,
  },
  {
    id: 'sopralluogo_invite',
    label: 'Invito al sopralluogo',
    description:
      'Diretta — quando il lead ha già mostrato interesse (portale ≥30s, scroll ≥60%) e va portato in agenda.',
    subject:
      'Sopralluogo gratuito su {{azienda}} — disponibilità prossima settimana?',
    body: `Buongiorno {{nome}},

vedo che ha avuto modo di guardare la proposta che le abbiamo inviato per {{azienda}}.

Il passo logico ora è il sopralluogo: 40 minuti sul posto, gratuito e senza impegno. Misuriamo tetto e ombre, validiamo i {{kwp}} kW che il sistema ha stimato, e le portiamo via un preventivo esatto entro 48h.

Le va bene se la chiamo nei prossimi giorni per fissarlo? In alternativa, può scegliere lei un orario rispondendo a questa email.

Cordialmente,
{{firma}}`,
  },
  {
    id: 'recap_post_visita',
    label: 'Dopo il sopralluogo',
    description:
      'Lead che ha già fatto il sopralluogo. Riassume i passi successivi senza chiedere ancora una decisione.',
    subject: 'Riepilogo sopralluogo — {{azienda}}',
    body: `Buongiorno {{nome}},

la ringrazio per il tempo dedicato al sopralluogo. Ricapitolo brevemente per sua comodità:

• Misurazione tetto: completata
• Verifica esposizione: ok per {{kwp}} kW
• Stima produttiva annuale: confermata

Sto preparando il preventivo esatto e glielo invio entro 48 ore lavorative. Se nel frattempo le viene un dubbio o una domanda, mi risponda pure direttamente qui.

A presto,
{{firma}}`,
  },
];

/**
 * Apply per-tenant overrides on top of the defaults. An override with
 * an empty/blank subject or body falls back to the default for that
 * field, so a half-filled override never produces an empty email.
 */
export function mergeFollowupTemplates(
  overrides: FollowupTemplateOverrides | null | undefined,
): FollowupTemplate[] {
  return FOLLOWUP_TEMPLATE_DEFAULTS.map((t) => {
    const ov = overrides?.[t.id];
    if (!ov) return t;
    return {
      ...t,
      subject: ov.subject && ov.subject.trim() ? ov.subject : t.subject,
      body: ov.body && ov.body.trim() ? ov.body : t.body,
    };
  });
}
