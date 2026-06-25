import type { QualificationReport } from '@/lib/data/qualification-report';

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-xl font-bold tracking-tight text-gray-900">{value}</div>
      <div className="text-xs text-gray-500">
        {label}
        {sub ? <span className="ml-1 text-gray-400">{sub}</span> : null}
      </div>
    </div>
  );
}

/**
 * Side-by-side comparison the tenant owner can read at a glance: sends that went
 * through the contact-qualification pipeline (NeverBounce validation + premium
 * contact) vs the un-validated "legacy" sends — and the lift in dossier-visit
 * rate the qualification delivers.
 */
export function QualificationReportPanel({ report }: { report: QualificationReport }) {
  const { qualified, legacy, lift } = report;
  if (qualified.sent === 0 && legacy.sent === 0) return null;

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">Riscontro qualifica contatto</h2>
        {lift && lift > 1 ? (
          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700">
            +{Math.round((lift - 1) * 100)}% tasso di visita con la qualifica ({lift}×)
          </span>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border-2 border-emerald-300 bg-emerald-50/40 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
              Qualificato
            </span>
            <span className="text-xs text-gray-600">NeverBounce + contatto premium</span>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Invii" value={String(qualified.sent)} />
            <Stat label="Visite" value={String(qualified.visited)} sub={`${qualified.visitRate}%`} />
            <Stat label="Appuntamenti" value={String(qualified.appointments)} />
          </div>
        </div>

        <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-gray-400 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
              Vecchio flusso
            </span>
            <span className="text-xs text-gray-600">senza validazione</span>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Invii" value={String(legacy.sent)} />
            <Stat label="Visite" value={String(legacy.visited)} sub={`${legacy.visitRate}%`} />
            <Stat label="Appuntamenti" value={String(legacy.appointments)} />
          </div>
        </div>
      </div>

      <p className="mt-3 text-xs text-gray-500">
        Confronto degli invii con e senza il sistema di qualifica del contatto (validazione
        NeverBounce + ricerca del referente). Un invio è «qualificato» quando l&apos;indirizzo è
        passato dalla validazione; «vecchio flusso» include gli invii non validati (incluso quando i
        crediti NeverBounce si sono esauriti).
      </p>
    </section>
  );
}
