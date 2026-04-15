import Link from 'next/link';

const NAV = [
  { href: '/leads', label: 'Leads' },
  { href: '/territories', label: 'Territori' },
  { href: '/campaigns', label: 'Campagne' },
  { href: '/analytics', label: 'Analytics' },
  { href: '/settings', label: 'Impostazioni' },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen grid-cols-[240px_1fr]">
      <aside className="border-r border-border bg-muted/30 p-4">
        <div className="mb-6">
          <h1 className="text-xl font-bold text-primary">SolarLead</h1>
          <p className="text-xs text-muted-foreground">Dashboard Installatore</p>
        </div>
        <nav className="space-y-1">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="block rounded-md px-3 py-2 text-sm hover:bg-accent"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="p-8">{children}</main>
    </div>
  );
}
