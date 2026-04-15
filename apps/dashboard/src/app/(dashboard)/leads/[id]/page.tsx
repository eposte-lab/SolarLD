type PageProps = { params: Promise<{ id: string }> };

export default async function LeadDetailPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold">Lead #{id}</h1>
        <p className="text-muted-foreground">Dettaglio lead + timeline eventi</p>
      </header>
      <section className="rounded-lg border border-border bg-card p-6 text-muted-foreground">
        Dettaglio non ancora implementato (Sprint 9).
      </section>
    </div>
  );
}
