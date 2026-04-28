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
};

export function HeroStat({
  label,
  value,
  unit,
  decimals = 0,
  accentColor = '#1F8F76',
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
    <div className="bento p-5">
      <div className="editorial-eyebrow flex items-center gap-2">
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: accentColor }}
        />
        {label}
      </div>
      <p className="mt-3 flex items-baseline gap-1.5 font-headline tracking-tightest text-on-surface">
        <span className="text-4xl font-semibold leading-none md:text-5xl">
          {formatted}
        </span>
        {isNumeric ? (
          <span className="text-sm font-medium text-on-surface-variant md:text-base">
            {unit}
          </span>
        ) : null}
      </p>
    </div>
  );
}
