import { ExternalLink } from 'lucide-react';

/**
 * Disabled "Pagina personale" affordance for a moderated tenant.
 *
 * The button stays visible (so the operator knows the dossier exists) but
 * is non-clickable, and on hover a styled overlay explains WHY: opening
 * the personal page from this dashboard would be recorded as lead
 * activity and pollute the prospect's real engagement tracking.
 *
 * Pure presentational + CSS-only tooltip (Tailwind `group` / `group-hover`)
 * so it works inside a Server Component — no client JS.
 */
export function PortalVisitBlocked({
  label,
  buttonClassName,
  iconSize = 13,
  strokeWidth = 2.25,
  align = 'right',
}: {
  /** Visible button text. */
  label: string;
  /** Tailwind classes for the disabled button itself (matches the live
   *  variant it replaces). */
  buttonClassName: string;
  iconSize?: number;
  strokeWidth?: number;
  /** Which edge the overlay aligns to (right for top-right header buttons). */
  align?: 'left' | 'right';
}) {
  const message =
    'Non puoi aprire la pagina personale da questa dashboard: la tua ' +
    'visita verrebbe registrata come attività del lead e falserebbe il ' +
    'tracciamento dei suoi movimenti reali.';

  return (
    <span className="group relative inline-flex">
      <span className={buttonClassName} aria-disabled="true" role="button">
        <ExternalLink size={iconSize} strokeWidth={strokeWidth} aria-hidden />
        {label}
      </span>
      <span
        role="tooltip"
        className={[
          'pointer-events-none absolute top-full z-50 mt-2 w-64 rounded-lg',
          'border border-outline-variant/40 bg-surface-container-highest',
          'px-3 py-2 text-left text-xs font-medium leading-snug text-on-surface',
          'opacity-0 shadow-lg transition-opacity duration-150',
          'group-hover:opacity-100',
          align === 'right' ? 'right-0' : 'left-0',
        ].join(' ')}
      >
        {message}
      </span>
    </span>
  );
}
