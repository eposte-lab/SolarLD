'use client';

import { API_URL, whatsappUrl } from '@/lib/api';

export function WhatsAppCta({
  slug,
  whatsappNumber,
  tenantName,
  brandColor,
}: {
  slug: string;
  whatsappNumber: string | null;
  tenantName: string;
  brandColor: string;
}) {
  const href = whatsappUrl(
    whatsappNumber,
    `Ciao ${tenantName}, vorrei più informazioni sul preventivo che mi avete inviato.`,
  );

  async function handleClick() {
    // Fire-and-forget — we don't block the user's WhatsApp redirect.
    try {
      const url = `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/whatsapp-click`;
      if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([], { type: 'application/json' }));
      } else {
        void fetch(url, { method: 'POST', keepalive: true }).catch(() => undefined);
      }
    } catch {
      /* ignore */
    }
  }

  if (!href) {
    return (
      <div className="rounded-lg bg-slate-100 p-6 text-center text-slate-500">
        Contatto WhatsApp non ancora configurato.
      </div>
    );
  }

  return (
    <a
      href={href}
      onClick={handleClick}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-center justify-center rounded-lg p-6 text-center text-lg font-semibold text-white shadow transition hover:opacity-90"
      style={{ backgroundColor: brandColor }}
    >
      💬 Parla con {tenantName} su WhatsApp
    </a>
  );
}
