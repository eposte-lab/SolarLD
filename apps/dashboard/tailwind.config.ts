/**
 * SolarLead dashboard Tailwind config — "Editorial Glass" design system.
 *
 * Dark-only, single-accent (amber editorial #F4A45C). Replaces the legacy
 * "Luminous Curator" light palette (forest green + terracotta + solar gold).
 *
 * Token naming preserves the MD3 surface/role nomenclature so existing
 * components keep working — but the values are recolored to a near-black
 * tonal hierarchy with one warm accent.
 *
 * See plan: ~/.claude/plans/shimmying-painting-backus.md (Sprint 7).
 */

import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: ['class'],
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
    '../../packages/ui/src/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        // ------------------------------------------------------------
        // Editorial Glass — surface tonal hierarchy (near-black)
        // ------------------------------------------------------------
        surface: '#0A0B0C',
        'surface-container-lowest': '#0F1112',
        'surface-container-low': '#14171A',
        'surface-container': '#1A1E22',
        'surface-container-high': '#22262B',
        'surface-container-highest': '#2B3036',
        'surface-dim': '#0A0B0C',
        'surface-bright': '#1A1E22',
        'surface-variant': '#22262B',

        // Text on surfaces
        'on-surface': '#ECEFF0',
        'on-surface-variant': '#8A9094',
        'on-surface-muted': '#5A6066',
        'on-background': '#ECEFF0',

        // Outlines (rare — most edges are tonal shifts, not borders)
        outline: '#2B3036',
        'outline-variant': 'rgba(255,255,255,0.08)',

        // Primary — Amber Editorial (sostituisce forest green)
        // L'unico accent del sistema. Usato per CTA, chart focused line,
        // delta negativi, focus ring, hover state.
        primary: '#F4A45C',
        'primary-dim': '#E8924A',
        'primary-container': '#B86F2C',
        'on-primary': '#1A1004',
        'on-primary-container': '#FFE8CC',

        // Secondary — desaturated grey for non-critical pills
        secondary: '#5A6066',
        'secondary-container': '#22262B',
        'on-secondary': '#ECEFF0',
        'on-secondary-container': '#ECEFF0',

        // Tertiary — alias of primary (keep token, route to amber)
        // Manteniamo il nome per backward-compat ma il colore è amber-dim.
        tertiary: '#E8924A',
        'tertiary-container': '#22262B',
        'on-tertiary': '#1A1004',
        'on-tertiary-container': '#F4A45C',

        // Error — desaturated red, leggibile su dark
        error: '#E85C5C',
        'error-container': '#3D1414',
        'on-error': '#FFE5E5',
        'on-error-container': '#FFB4B4',

        // Success — verde desaturato, solo per status semantici positivi
        // (won, online, healthy). Usato sparingly: la regola è "amber per
        // tutto ciò che richiede attenzione, success per ciò che non la richiede".
        success: '#6FCF97',
        'success-container': '#0F2418',
        'on-success': '#0A1A10',
        'on-success-container': '#A7E2BC',

        // ------------------------------------------------------------
        // Legacy shadcn HSL tokens (mapped to dark surfaces)
        // ------------------------------------------------------------
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
      },
      fontFamily: {
        headline: ['var(--font-headline)', 'Plus Jakarta Sans', 'sans-serif'],
        body: ['var(--font-body)', 'Manrope', 'sans-serif'],
        sans: ['var(--font-body)', 'Manrope', 'sans-serif'],
      },
      borderRadius: {
        DEFAULT: '0.25rem',
        lg: '1rem', // nested bento items
        xl: '1.5rem', // main bento containers
        '2xl': '2rem', // hero cards / glass panels
        full: '9999px',
      },
      boxShadow: {
        // Ambient — su dark è una luce soft, non un'ombra
        ambient:
          '0 30px 50px -5px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.04)',
        'ambient-sm':
          '0 20px 30px -5px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.04)',
        rail: '30px 0 50px -20px rgba(0,0,0,0.50)',
        // Amber focus ring (primary @ 28%)
        'gradient-focus': '0 0 0 4px rgba(244,164,92,0.28)',
        // Amber subtle glow for hero numbers
        'editorial-glow': '0 0 32px rgba(244,164,92,0.18)',
      },
      backgroundImage: {
        // Hero gradient — amber wash su superficie
        'gradient-primary':
          'linear-gradient(135deg, #F4A45C 0%, #B86F2C 100%)',
        // Gradient delicato bianco→amber per headline hero
        'gradient-headline':
          'linear-gradient(135deg, #ECEFF0 0%, #F4A45C 100%)',
        // Glass tint warm — usato dietro card flottanti su map
        'glass-warm':
          'linear-gradient(135deg, rgba(244,164,92,0.06) 0%, rgba(0,0,0,0.30) 100%)',
        // Noise overlay PNG-equivalent via SVG turbulence — riduce banding
        noise:
          "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/></filter><rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/></svg>\")",
      },
      backdropBlur: {
        'glass-sm': '16px',
        glass: '28px',
        'glass-lg': '40px',
      },
      letterSpacing: {
        tighter: '-0.02em',
        tightest: '-0.035em', // hero numbers
      },
      keyframes: {
        numericReveal: {
          '0%': { opacity: '0', transform: 'scale(0.95) translateY(4px)' },
          '100%': { opacity: '1', transform: 'scale(1) translateY(0)' },
        },
        radarPulse: {
          '0%': { transform: 'scale(0.8)', opacity: '0.8' },
          '70%': { transform: 'scale(2.5)', opacity: '0' },
          '100%': { transform: 'scale(2.5)', opacity: '0' },
        },
      },
      animation: {
        'numeric-reveal': 'numericReveal 0.6s cubic-bezier(0.22,1,0.36,1) both',
      },
    },
  },
  plugins: [],
};

export default config;
