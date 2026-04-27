import type { Metadata } from 'next';
import { Manrope, Plus_Jakarta_Sans } from 'next/font/google';
import './globals.css';

/**
 * Editorial duo per DESIGN.md §3:
 *   - Plus Jakarta Sans → headlines (geometric, tight tracking)
 *   - Manrope           → body + labels (tech-focused, dense data)
 *
 * Both are wired as CSS variables so Tailwind `font-headline` /
 * `font-body` resolve without needing to import the classes directly.
 */
const headline = Plus_Jakarta_Sans({
  subsets: ['latin'],
  weight: ['500', '600', '700', '800'],
  variable: '--font-headline',
  display: 'swap',
});

const body = Manrope({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-body',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'SolarLead Dashboard',
  description: 'Agentic Lead Generation for Solar Installers',
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="it"
      className={`dark ${headline.variable} ${body.variable}`}
      suppressHydrationWarning
    >
      <body className="min-h-screen bg-surface font-body text-on-surface antialiased">
        {children}
      </body>
    </html>
  );
}
