/**
 * Invio detail — pagina dettaglio di un singolo outreach send.
 *
 * Layout:
 *   Breadcrumb  ← Invii
 *   Header       "Invio #N · [channel chip] · [status chip]" + subject
 *   Grid 2 col   Colonna sinistra: Media inviata
 *                Colonna destra:   Timeline engagement + info invio
 *   Footer       Link "Vedi lead completo →"
 */

import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { getOutreachSendDetail } from '@/lib/data/campaigns';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, formatDate, relativeTime } from '@/lib/utils';

export const dynamic = 'force-dynamic';

type PageProps = { params: Promise<{ id: string }> };

export default async function InvioDetailPage({ params }: PageProps) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const { id } = await params;
  const send = await getOutreachSendDetail(id);
  if (!send) notFound();

  const lead = send.leads;
  const businessName =
    lead?.subjects?.business_name ||
    lead?.subjects?.decision_maker_name ||
    '—';

  // Prefer the snapshotted media on the send itself (so this page
  // shows what the prospect ACTUALLY received, not whatever the lead
  // has been re-rendered to since); fall back to lead's current URLs.
  // Three-tier fallback (video → GIF → static after image) so the
  // operator sees a visual asset whenever ANY render artefact exists.
  const gifUrl = send.rendering_gif_url ?? lead?.rendering_gif_url ?? null;
  const videoUrl = send.rendering_video_url ?? lead?.rendering_video_url ?? null;
  const imageUrl = send.rendering_image_url ?? lead?.rendering_image_url ?? null;

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <Link
        href="/invii"
        className="text-xs font-medium text-on-surface-variant transition-colors hover:text-primary"
      >
        ← Invii
      </Link>

      {/* Header */}
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="font-headline text-3xl font-bold tracking-tighter">
            Invio #{send.sequence_step}
          </h1>
          <ChannelChip channel={send.channel} />
          <StatusChip status={send.status} />
        </div>
        {send.email_subject && (
          <p className="max-w-2xl text-sm text-on-surface-variant">
            {send.email_subject}
          </p>
        )}
      </header>

      {/* Main grid */}
      <BentoGrid cols={2}>
        {/* ── Left: Media inviata ── */}
        <BentoCard padding="tight">
          <div className="px-2 pb-3 pt-2">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Media inviata
            </p>
            <h2 className="font-headline text-xl font-bold tracking-tighter">
              Rendering
            </h2>
          </div>

          <div className="overflow-hidden rounded-xl">
            {videoUrl ? (
              // eslint-disable-next-line jsx-a11y/media-has-caption
              <video
                src={videoUrl}
                poster={gifUrl ?? imageUrl ?? undefined}
                controls
                muted
                loop
                playsInline
                className="w-full rounded-xl object-cover"
              />
            ) : gifUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={gifUrl}
                alt="GIF rendering inviata"
                className="w-full rounded-xl object-cover"
              />
            ) : imageUrl ? (
              // Static after-image fallback — same artefact the email
              // body uses when video render is bypassed. Without this
              // the section was empty whenever Replicate / Kling failed
              // even if the panel-paint succeeded.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imageUrl}
                alt="Foto del tetto con pannelli (statica)"
                className="w-full rounded-xl object-cover"
              />
            ) : (
              <div className="flex min-h-[200px] items-center justify-center rounded-xl bg-surface-container-low px-6 py-10 text-center text-sm text-on-surface-variant">
                Nessun media — il rendering non era disponibile al momento
                dell&apos;invio.
              </div>
            )}
          </div>
        </BentoCard>

        {/* ── Right: Timeline + Info ── */}
        <div className="space-y-4">
          {/* Timeline card */}
          <BentoCard padding="tight">
            <div className="px-2 pb-3 pt-2">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Engagement
              </p>
              <h2 className="font-headline text-xl font-bold tracking-tighter">
                Timeline
              </h2>
            </div>
            <ol className="space-y-0">
              <TimelineRow
                label="Inviato"
                value={send.sent_at}
                active={!!send.sent_at}
              />
              <TimelineRow
                label="Consegnato"
                value={lead?.outreach_delivered_at ?? null}
                active={!!lead?.outreach_delivered_at}
              />
              <TimelineRow
                label="Aperto"
                value={lead?.outreach_opened_at ?? null}
                active={!!lead?.outreach_opened_at}
              />
              <TimelineRow
                label="Cliccato"
                value={lead?.outreach_clicked_at ?? null}
                active={!!lead?.outreach_clicked_at}
              />
            </ol>
          </BentoCard>

          {/* Info card */}
          <BentoCard padding="tight">
            <div className="px-2 pb-3 pt-2">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Dettagli
              </p>
              <h2 className="font-headline text-xl font-bold tracking-tighter">
                Info invio
              </h2>
            </div>
            <dl className="space-y-0">
              <InfoRow
                label="Lead"
                value={
                  <Link
                    href={`/leads/${send.lead_id}`}
                    className="font-semibold text-primary hover:underline"
                  >
                    {businessName}
                  </Link>
                }
              />
              <InfoRow label="Step" value={`#${send.sequence_step}`} />
              {send.template_id && (
                <InfoRow label="Template" value={send.template_id} />
              )}
              {send.email_message_id && (
                <InfoRow
                  label="Message ID"
                  value={
                    <span
                      className="font-mono text-xs"
                      title={send.email_message_id}
                    >
                      {send.email_message_id.slice(0, 20)}
                      {send.email_message_id.length > 20 ? '…' : ''}
                    </span>
                  }
                />
              )}
              <InfoRow
                label="Costo"
                value={
                  send.cost_cents > 0
                    ? `€ ${(send.cost_cents / 100).toFixed(2)}`
                    : '—'
                }
              />
              {send.experiment_variant && (
                <InfoRow
                  label="A/B variante"
                  value={send.experiment_variant}
                />
              )}
            </dl>
          </BentoCard>
        </div>
      </BentoGrid>

      {/* Footer CTA */}
      <div className="pt-2">
        <Link
          href={`/leads/${send.lead_id}`}
          className="inline-flex items-center gap-1 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          Vedi lead completo →
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Presentational atoms
// ---------------------------------------------------------------------------

function ChannelChip({ channel }: { channel: string }) {
  const styles: Record<string, string> = {
    email: 'bg-primary-container/60 text-on-primary-container',
    postal: 'bg-tertiary-container/60 text-on-tertiary-container',
    whatsapp: 'bg-surface-container-highest text-on-surface',
  };
  const labels: Record<string, string> = {
    email: 'Email',
    postal: 'Postale',
    whatsapp: 'WhatsApp',
  };
  return (
    <span
      className={cn(
        'inline-flex rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest',
        styles[channel] ?? 'bg-surface-container text-on-surface-variant',
      )}
    >
      {labels[channel] ?? channel}
    </span>
  );
}

function StatusChip({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-surface-container-high text-on-surface-variant',
    sent: 'bg-surface-container-highest text-on-surface',
    delivered: 'bg-primary-container text-on-primary-container',
    failed: 'bg-secondary-container text-on-secondary-container',
    cancelled: 'bg-surface-container text-on-surface-variant opacity-70',
  };
  const labels: Record<string, string> = {
    pending: 'In coda',
    sent: 'Inviato',
    delivered: 'Consegnato',
    failed: 'Fallito',
    cancelled: 'Cancellato',
  };
  return (
    <span
      className={cn(
        'inline-flex rounded-md px-2 py-0.5 text-xs font-medium',
        styles[status] ?? 'bg-surface-container text-on-surface-variant',
      )}
    >
      {labels[status] ?? status}
    </span>
  );
}

function TimelineRow({
  label,
  value,
  active,
}: {
  label: string;
  value: string | null;
  active: boolean;
}) {
  return (
    <li
      className="flex items-center justify-between px-2 py-3 text-sm"
      style={{ boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }}
    >
      <span
        className={cn(
          'flex items-center gap-2',
          active ? 'font-semibold text-on-surface' : 'text-on-surface-variant',
        )}
      >
        <span
          className={cn(
            'inline-block h-2 w-2 rounded-full',
            active ? 'bg-primary' : 'bg-outline-variant',
          )}
        />
        {label}
      </span>
      <span
        className={cn(
          'text-xs tabular-nums',
          active ? 'text-on-surface' : 'text-on-surface-variant',
        )}
      >
        {value ? (
          <span title={formatDate(value)}>{relativeTime(value)}</span>
        ) : (
          '—'
        )}
      </span>
    </li>
  );
}

function InfoRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div
      className="flex items-center justify-between px-2 py-3 text-sm"
      style={{ boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }}
    >
      <dt className="text-on-surface-variant">{label}</dt>
      <dd className="text-right font-semibold text-on-surface">{value}</dd>
    </div>
  );
}
