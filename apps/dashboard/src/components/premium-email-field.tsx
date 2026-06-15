/**
 * Premium decision-maker contact UI.
 *
 * A contact is "premium" when its email was upgraded to a named decision-maker
 * (subjects.decision_maker_email_source === 'premium_finder') — by the
 * automatic funnel step, the on-demand button, or the batch — instead of a
 * generic info@ inbox.
 *
 * - PremiumBadge: a small pill for the contatti / invii lists.
 * - PremiumEmailField: the lead-detail email, wrapped in a rounded box with a
 *   luminous line travelling its perimeter (.premium-glow in globals.css) +
 *   a hover tooltip explaining it's a researched, non-generic contact.
 */

import { BadgeCheck } from 'lucide-react';

export const PREMIUM_EMAIL_SOURCE = 'premium_finder';

const PREMIUM_TOOLTIP =
  'Contatto premium — referente di grado superiore, ricavato con un processo di ricerca contatto più ampio. Non è una casella generica (info@, contatti@): è un destinatario studiato.';

/** True when a subject's email is a premium-finder decision-maker upgrade. */
export function isPremiumSource(source: string | null | undefined): boolean {
  return source === PREMIUM_EMAIL_SOURCE;
}

/** Small "Verificato" pill for list rows (contatti, invii). Vendor-neutral. */
export function PremiumBadge({ className }: { className?: string }) {
  return (
    <span
      title={PREMIUM_TOOLTIP}
      className={
        'inline-flex items-center gap-0.5 rounded-full bg-primary-container px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-on-primary-container' +
        (className ? ` ${className}` : '')
      }
    >
      <BadgeCheck size={10} strokeWidth={2.5} aria-hidden />
      Verificato
    </span>
  );
}

/**
 * The lead-detail email, with the perimeter-light "premium" treatment.
 * Falls back to a plain mailto link when the contact isn't premium.
 */
export function PremiumEmailField({
  email,
  premium,
}: {
  email: string;
  premium: boolean;
}) {
  if (!premium) {
    return (
      <a
        href={`mailto:${email}`}
        className="hover:underline focus:underline focus:outline-none"
      >
        {email}
      </a>
    );
  }
  return (
    <span
      title={PREMIUM_TOOLTIP}
      className="premium-glow group inline-flex max-w-full items-center gap-1.5 rounded-[0.625rem] bg-surface-container-low px-2.5 py-1 align-middle"
    >
      <BadgeCheck
        size={13}
        strokeWidth={2.5}
        aria-hidden
        className="shrink-0 text-primary"
      />
      <a
        href={`mailto:${email}`}
        className="truncate font-semibold text-on-surface hover:underline focus:underline focus:outline-none"
      >
        {email}
      </a>
      <span className="shrink-0 text-[9px] font-bold uppercase tracking-wide text-primary">
        Premium
      </span>
    </span>
  );
}
