/**
 * SolarLead dashboard Tailwind config — "Luminous Curator" design
 * system (Sprint 9, Fase B).
 *
 * Token naming mirrors Material Design 3 surface/role tokens from
 * DESIGN.md so we can copy-paste snippets from the stitch mockups.
 * HSL-backed CSS variables are still available (legacy shadcn-style)
 * but the canonical surface is the named token below.
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
        // Luminous Curator palette (MD3 role tokens)
        // ------------------------------------------------------------
        // Surface layers — tonal layering, not borders
        surface: '#f4f7f6',
        'surface-container-lowest': '#ffffff',
        'surface-container-low': '#eef1f0',
        'surface-container': '#e5e9e8',
        'surface-container-high': '#dee3e2',
        'surface-container-highest': '#d8dedd',
        'surface-dim': '#cfd6d5',
        'surface-bright': '#f4f7f6',
        'surface-variant': '#d8dedd',

        // Text on surfaces — never pure black
        'on-surface': '#2b2f2f',
        'on-surface-variant': '#585c5c',
        'on-background': '#2b2f2f',

        // Outlines (use sparingly — Ghost Border only)
        outline: '#747877',
        'outline-variant': '#aaaead',

        // Primary — forest green for actions
        primary: '#006a37',
        'primary-dim': '#005c2f',
        'primary-container': '#6afea0',
        'on-primary': '#ccffd5',
        'on-primary-container': '#005f31',

        // Secondary — terracotta for urgency/heat
        secondary: '#b22200',
        'secondary-container': '#ffc4b7',
        'on-secondary': '#ffefec',
        'on-secondary-container': '#8d1900',

        // Tertiary — solar gold for high-value highlights
        tertiary: '#795500',
        'tertiary-container': '#fdbb31',
        'on-tertiary': '#fff1de',
        'on-tertiary-container': '#563b00',

        // Error
        error: '#b31b25',
        'error-container': '#fb5151',
        'on-error': '#ffefee',
        'on-error-container': '#570008',

        // ------------------------------------------------------------
        // Legacy shadcn HSL tokens (still used by a few components)
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
        // Headlines: geometric + editorial
        headline: ['var(--font-headline)', 'Plus Jakarta Sans', 'sans-serif'],
        // Body + labels: dense tech-focused
        body: ['var(--font-body)', 'Manrope', 'sans-serif'],
        sans: ['var(--font-body)', 'Manrope', 'sans-serif'],
      },
      borderRadius: {
        DEFAULT: '0.25rem',
        lg: '1rem', // nested bento items
        xl: '1.5rem', // main bento containers
        full: '9999px',
      },
      boxShadow: {
        // Ambient shadow — tucked, soft, follows DESIGN.md §4
        ambient: '0 30px 50px -5px rgba(43,47,47,0.06)',
        'ambient-sm': '0 20px 30px -5px rgba(43,47,47,0.04)',
        // Side-nav ambient bleed
        rail: '30px 0 50px -20px rgba(43,47,47,0.04)',
        // Gradient-button focus ring (primary @ 20%)
        'gradient-focus': '0 0 0 4px rgba(0,106,55,0.20)',
      },
      backgroundImage: {
        // Signature primary gradient (CTAs, chart fills)
        'gradient-primary': 'linear-gradient(135deg, #006a37 0%, #6afea0 100%)',
        // Heat scale (primary → tertiary → secondary)
        'gradient-heat':
          'linear-gradient(135deg, #006a37 0%, #fdbb31 55%, #b22200 100%)',
      },
      backdropBlur: {
        // Glassmorphism for floating overlays only
        glass: '24px',
      },
      letterSpacing: {
        // DESIGN.md §3: -2% tracking on large headlines
        tighter: '-0.02em',
      },
    },
  },
  plugins: [],
};

export default config;
