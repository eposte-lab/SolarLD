/**
 * HeroStat — KPI gigante in stile editorial.
 *
 * Composizione "big number + dimmed unit": il valore numerico vive in
 * font-headline tight-tracked, l'unità (kWp, €, anni, t) appare a 60%
 * opacity sulla stessa riga in dimensione molto più piccola — è la
 * stessa lingua visiva del dashboard (size="hero" su KpiChipCard).
 *
 * Quando ``value`` è ``null``/``undefined``/``NaN`` mostriamo "—" per
 * evitare di rendere "NaN kWp" se il backend non ha calcolato il dato.
 */

type Props = {
  label: string;
  /** Numeric value to format with thousand separator + N decimal places. */
  value: number | null | undefined;
  unit: string;
  /** Decimals to render on the value (default 0). */
  decimals?: number;
  /** Brand color reused for the eyebrow accent dot. */
  accentColor?: string;
  /** Optional second-line caption — used to clarify ambiguous units
   *  (es. "Potenza installabile: 75 kWp" → caption "≈ 107.590 kWh/anno"). */
  caption?: string | null;
  /** Optional prefix rendered before the number at near-headline size —
   *  used for currency display ("€ 34.881"), so the symbol carries the
   *  same visual weight as the number instead of disappearing into the
   *  small suffix. */
  prefix?: string;
};

export function HeroStat({
  label,
  value,
  unit,
  decimals = 0,
  accentColor = '#1F8F76',
  caption,
  prefix,
}: Props) {
  const isNumeric =
    value !== null && value !== undefined && !Number.isNaN(value);
  const formatted = isNumeric
    ? value!.toLocaleString('it-IT', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : '—';

  return (
    <div
      className="relative overflow-hidden rounded-2xl border px-4 py-3 transition-shadow hover:shadow-ambient-md"
      style={{
        borderColor: `${accentColor}22`,
        backgroundColor: `${accentColor}08`,
      }}
    >
      {/* Striscia d'accento brand in alto — il gesto che dà identità
          alla card senza saturare il riquadro. */}
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-1"
        style={{ backgroundColor: accentColor }}
      />
      <p
        className="mt-0.5 text-[10px] font-bold uppercase tracking-[0.16em]"
        style={{ color: accentColor }}
      >
        {label}
      </p>
      {/* Numero + unità su una sola riga: niente flex-wrap, così quando
          "kWh/anno" non entra dietro al numero il design scala il
          contenuto (Tailwind text-3xl/4xl) invece di mandare l'unità
          a capo allargando inutilmente l'altezza della card. */}
      <p className="mt-1.5 flex items-baseline gap-1.5 whitespace-nowrap font-headline tracking-tightest">
        {prefix && isNumeric ? (
          <span
            className="text-2xl font-bold leading-none md:text-3xl"
            style={{ color: accentColor }}
          >
            {prefix}
          </span>
        ) : null}
        <span
          className="text-3xl font-bold leading-none md:text-4xl"
          style={{ color: accentColor }}
        >
          {formatted}
        </span>
        {isNumeric ? (
          <span className="text-xs font-semibold text-on-surface-variant md:text-sm">
            {unit}
          </span>
        ) : null}
      </p>
      {caption ? (
        <p className="mt-1 text-[11px] leading-snug text-on-surface-variant">
          {caption}
        </p>
      ) : null}
    </div>
  );
}
