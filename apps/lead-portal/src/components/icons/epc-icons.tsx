/**
 * epc-icons — icone line-art per la sezione EPC del portale.
 *
 * SVG inline, stroke `currentColor`, stile coerente col brochure Total
 * Trade. Sostituiscono le emoji usate in precedenza (🏗️ ⚡ 🔑 …).
 */

import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base({ size = 24, ...rest }: IconProps) {
  return {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.75,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
    ...rest,
  };
}

/** Euro barrato — zero spese d'investimento. */
export function IconZeroInvest(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M16.5 8.2A5 5 0 1 0 16.5 15.8" />
      <path d="M5.5 10.8h7.2M5.5 13.4h6" />
      <path d="M4.2 19.8 19.8 4.2" />
    </svg>
  );
}

/** Euro con freccia in giù — risparmio immediato in bolletta. */
export function IconImmediateSaving(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M13.5 6.5A4.4 4.4 0 1 0 13.5 15.3" />
      <path d="M4 9.4h6.6M4 11.8h5.4" />
      <path d="M18.5 8.5v9M18.5 17.5l-3-3M18.5 17.5l3-3" />
    </svg>
  );
}

/** Chiave — a fine contratto l'impianto diventa tuo. */
export function IconOwnership(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="8" cy="8" r="4.2" />
      <path d="M10.9 10.9 20 20" />
      <path d="M17 17l2.4 2.4M14.4 19.2l2 2" />
    </svg>
  );
}

/** Cerchio col più — vantaggio (stile slide Plenitude). */
export function IconPlus(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v8M8 12h8" />
    </svg>
  );
}

/** Cerchio col meno — svantaggio (stile slide Plenitude). */
export function IconMinus(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12h8" />
    </svg>
  );
}
