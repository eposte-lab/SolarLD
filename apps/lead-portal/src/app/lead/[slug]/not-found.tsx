export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold">Link non valido</h1>
        <p className="mt-2 text-sm text-slate-600">
          Questo link non corrisponde a nessun dossier attivo. Potrebbe essere scaduto o non esistere.
        </p>
      </div>
    </main>
  );
}
