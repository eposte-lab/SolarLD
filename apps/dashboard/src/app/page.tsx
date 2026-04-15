import Link from 'next/link';

export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="max-w-2xl text-center">
        <h1 className="mb-4 text-5xl font-bold text-primary">SolarLead</h1>
        <p className="mb-8 text-lg text-muted-foreground">
          Agentic Lead Generation Platform per Installatori Fotovoltaici
        </p>
        <div className="flex gap-4 justify-center">
          <Link
            href="/login"
            className="rounded-md bg-primary px-6 py-3 text-primary-foreground font-medium hover:bg-primary/90"
          >
            Login Installatore
          </Link>
          <Link
            href="/leads"
            className="rounded-md border border-border px-6 py-3 font-medium hover:bg-accent"
          >
            Dashboard
          </Link>
        </div>
        <p className="mt-8 text-xs text-muted-foreground">v0.1.0 — Sprint 0 Foundation</p>
      </div>
    </main>
  );
}
