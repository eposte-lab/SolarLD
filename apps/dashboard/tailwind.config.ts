/**
 * SolarLead dashboard Tailwind config — "Liquid Glass" design system (V2).
 *
 * Dark-only, single-accent (mint editorial #6FCF97). Replaces V1 amber.
 * Aesthetic reference: iOS 18 / visionOS Liquid Glass — heavy backdrop
 * blur (40-60px) + saturate(180%) + specular highlight on top edge of
 * cards, soft fluid color washes, sustainability-coherent mint accent.
 *
 * Token naming preserves MD3 surface/role nomenclature so existing
 * components keep working — only the values shift.
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
        // Liquid Glass — surface tonal hierarchy (cool near-black)
        // Slight blue-green tint per coerenza con accent mint.
        // ------------------------------------------------------------
        surface: '#07090A',
        'surface-container-lowest': '#0B0F11',
        'surface-container-low': '#10161A',
        'surface-container': '#161D22',
        'surface-container-high': '#1E262C',
        'surface-container-highest': '#283038',
        'surface-dim': '#07090A',
        'surface-bright': '#161D22',
        'surface-variant': '#1E262C',

        // Text on surfaces
        'on-surface': '#ECEFF0',
        'on-surface-variant': '#8A9499',
        'on-surface-muted': '#5A6066',
        'on-background': '#ECEFF0',

        // Outlines — usate sparingly, l'edge è quasi sempre tonal o specular
        outline: '#283038',
        'outline-variant': 'rgba(255,255,255,0.08)',

        // Primary — Mint Editorial (#6FCF97 desaturato sustainability)
        // L'unico accent del sistema. Usato per CTA, focused chart line,
        // active nav pill, focus ring, success/won status, hover state.
        primary: '#6FCF97',
        'primary-dim': '#5BB880',
        'primary-bright': '#8FE5B0',
        'primary-container': '#1A3F2A',
        'on-primary': '#04140A',
        'on-primary-container': '#C7EFD5',

        // Secondary — desaturated grey for non-critical pills
        secondary: '#5A6066',
        'secondary-container': '#1E262C',
        'on-secondary': '#ECEFF0',
        'on-secondary-container': '#ECEFF0',

        // Tertiary — alias of primary (keep token, route to mint-dim)
        tertiary: '#5BB880',
        'tertiary-container': '#1E262C',
        'on-tertiary': '#04140A',
        'on-tertiary-container': '#6FCF97',

        // Error — desaturated red, leggibile su dark
        error: '#E85C5C',
        'error-container': '#3D1414',
        'on-error': '#FFE5E5',
        'on-error-container': '#FFB4B4',

        // Warning — amber tenue, single-use semantico (warm-up, cap)
        // È l'unico residuo di amber: serve a distinguere "attenzione"
        // da "azione positiva" (mint). Usato sparingly nei badge.
        warning: '#F4A45C',
        'warning-container': '#3A2310',
        'on-warning': '#1A1004',
        'on-warning-container': '#FFD9B0',

        // Success — alias di primary (semanticamente coerente: mint = ok)
        success: '#6FCF97',
        'success-container': '#1A3F2A',
        'on-success': '#04140A',
        'on-success-container': '#C7EFD5',

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
        DEFAULT: '0.375rem',
        lg: '1rem',
        xl: '1.5rem',
        '2xl': '2rem',
        '3xl': '2.5rem',
        full: '9999px',
      },
      boxShadow: {
        // Ambient — deep soft shadow + specular top-edge highlight
        ambient:
          '0 30px 60px -10px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.05), inset 0 1px 0 rgba(255,255,255,0.08)',
        'ambient-sm':
          '0 16px 32px -8px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.05), inset 0 1px 0 rgba(255,255,255,0.06)',
        rail: '30px 0 50px -20px rgba(0,0,0,0.50)',
        // Mint focus ring (primary @ 28%)
        'gradient-focus': '0 0 0 4px rgba(111,207,151,0.28)',
        // Mint subtle glow per hero KPI
        'editorial-glow': '0 0 40px rgba(111,207,151,0.22)',
        // Liquid glass — heavy outer shadow + inset specular edge
        'liquid-glass':
          '0 24px 48px -12px rgba(0,0,0,0.50), 0 0 0 1px rgba(255,255,255,0.06), inset 0 1px 0 rgba(255,255,255,0.12), inset 0 -1px 0 rgba(255,255,255,0.04)',
        'liquid-glass-lg':
          '0 40px 80px -20px rgba(0,0,0,0.60), 0 0 0 1px rgba(255,255,255,0.08), inset 0 1px 0 rgba(255,255,255,0.16), inset 0 -1px 0 rgba(255,255,255,0.04)',
      },
      backgroundImage: {
        // Hero gradient — mint wash su superficie
        'gradient-primary':
          'linear-gradient(135deg, #6FCF97 0%, #5BB880 100%)',
        // Gradient delicato bianco→mint per headline hero
        'gradient-headline':
          'linear-gradient(135deg, #ECEFF0 0%, #6FCF97 100%)',
        // Glass tint cool — usato dietro card flottanti su map
        'glass-mint':
          'linear-gradient(135deg, rgba(111,207,151,0.06) 0%, rgba(0,0,0,0.30) 100%)',
        // Specular top-edge highlight per liquid glass cards
        'glass-specular':
          'linear-gradient(180deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0.02) 24%, transparent 50%)',
        // Noise overlay PNG-equivalent via SVG turbulence — riduce banding
        noise:
          "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/></filter><rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/></svg>\")",
      },
      backdropBlur: {
        'glass-xs': '12px',
        'glass-sm': '20px',
        glass: '36px',
        'glass-lg': '52px',
        'glass-xl': '72px',
      },
      backdropSaturate: {
        liquid: '1.8',
      },
      letterSpacing: {
        tighter: '-0.02em',
        tightest: '-0.035em',
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
        liquidShine: {
          '0%, 100%': { transform: 'translateX(-30%)', opacity: '0' },
          '50%': { transform: 'translateX(30%)', opacity: '0.6' },
        },
      },
      animation: {
        'numeric-reveal': 'numericReveal 0.6s cubic-bezier(0.22,1,0.36,1) both',
        'liquid-shine': 'liquidShine 6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};

export default config;
