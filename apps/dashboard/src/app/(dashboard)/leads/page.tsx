export default function LeadsPage() {
  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Leads</h1>
          <p className="text-muted-foreground">Tutti i lead qualificati generati dal sistema.</p>
        </div>
      </header>

      <div className="rounded-lg border border-border bg-card p-8 text-center text-muted-foreground">
        Nessun lead da mostrare. I lead verranno popolati quando Hunter, Identity, Scoring e Creative agents saranno operativi.
      </div>
    </div>
  );
}
