/**
 * GradientButton — primary CTA styled per DESIGN.md §5.
 *
 *   "Rounded `full` (9999px). Gradient from `primary` to
 *    `primary-container` with `on-primary` text."
 *
 * A thin `secondary` variant is included for the "No" counterpart:
 * surface-container-highest background, on-surface text, no border.
 * Also supports rendering as a Next.js `<Link>` via `asChild`-style
 * `href` prop — saves wrapping in a nested `<Link><button/></Link>`.
 */

import Link, { type LinkProps as NextLinkProps } from 'next/link';
import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

type Variant = 'primary' | 'secondary' | 'ghost';
type Size = 'sm' | 'md' | 'lg';

const VARIANT: Record<Variant, string> = {
  primary:
    'bg-gradient-primary text-on-primary font-bold shadow-ambient-sm hover:opacity-95 focus-visible:shadow-gradient-focus',
  secondary:
    'bg-surface-container-highest text-on-surface font-semibold hover:bg-surface-container-high',
  ghost:
    'bg-transparent text-on-surface hover:bg-surface-container-low font-medium',
};

const SIZE: Record<Size, string> = {
  sm: 'px-4 py-1.5 text-xs',
  md: 'px-5 py-2.5 text-sm',
  lg: 'px-6 py-3 text-base',
};

const BASE =
  'inline-flex items-center justify-center gap-2 rounded-full transition-all duration-150 outline-none disabled:opacity-50 disabled:cursor-not-allowed';

interface SharedProps {
  variant?: Variant;
  size?: Size;
  className?: string;
  children: React.ReactNode;
}

type ButtonProps = SharedProps &
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, keyof SharedProps> & {
    href?: undefined;
  };

type LinkProps = SharedProps &
  Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof SharedProps | 'href'> & {
    /**
     * Accepts any href. We cast to Next.js' typedRoutes `Route` at the
     * call site below — this component is infrastructure and predates
     * the strongly-typed routes, so we opt out explicitly.
     */
    href: string;
  };

export type GradientButtonProps = ButtonProps | LinkProps;

export function GradientButton(props: GradientButtonProps) {
  const {
    variant = 'primary',
    size = 'md',
    className,
    children,
    ...rest
  } = props;

  const classes = cn(BASE, VARIANT[variant], SIZE[size], className);

  if ('href' in rest && typeof rest.href === 'string') {
    const { href, ...anchorRest } = rest;
    return (
      <Link
        href={href as NextLinkProps<string>['href']}
        className={classes}
        {...anchorRest}
      >
        {children}
      </Link>
    );
  }

  const buttonRest = rest as ButtonHTMLAttributes<HTMLButtonElement>;
  return (
    <button className={classes} {...buttonRest}>
      {children}
    </button>
  );
}
