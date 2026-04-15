export default function DashboardOverview() {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold">Panoramica</h1>
        <p className="text-muted-foreground">Riepilogo operativo del mese corrente.</p>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[
          { label: 'Leads inviati (mese)', value: '—' },
          { label: 'Hot leads', value: '—' },
          { label: 'Appuntamenti', value: '—' },
          { label: 'Contratti firmati', value: '—' },
        ].map((k) => (
          <div key={k.label} className="rounded-lg border border-border bg-card p-4">
            <p className="text-xs uppercase text-muted-foreground">{k.label}</p>
            <p className="mt-2 text-3xl font-semibold">{k.value}</p>
          </div>
        ))}
      </section>

      <section className="rounded-lg border border-border bg-card p-6">
        <h2 className="mb-4 text-xl font-semibold">Top 10 Hot Leads</h2>
        <p className="text-sm text-muted-foreground">
          Nessun lead ancora. Il sistema è in fase di Sprint 0 — collega un territorio per iniziare.
        </p>
      </section>
    </div>
  );
}
