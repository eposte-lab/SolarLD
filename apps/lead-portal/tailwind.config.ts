import type { Config } from 'tailwindcss';

/**
 * Lead-portal theme.
 *
 * Sprint 8 Fase A.3: ports a focused subset of the dashboard
 * "Liquid Glass / Editorial" tokens so the public portal feels
 * coherent with the operator dashboard without bloating this config
 * with every internal token.
 *
 * Surface stack mirrors the consumer-facing variant (light, warm),
 * not the dashboard's dark variant: this page is shown to leads,
 * who expect a clean, premium-but-friendly dossier.
 */
const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#0F766E',
          light: '#14B8A6',
          dark: '#134E4A',
        },
        primary: {
          DEFAULT: '#1F8F76',          // mint editorial
          soft: 'rgba(31, 143, 118, 0.12)',
          ring: 'rgba(31, 143, 118, 0.25)',
        },
        surface: {
          DEFAULT: '#F4F7F6',
          container: '#FFFFFF',
          'container-low': '#F8FAFA',
          'container-high': '#EDF2F1',
        },
        on: {
          surface: '#0F1A18',
          'surface-variant': '#5A6A65',
          'surface-muted': '#8A968F',
          primary: '#FFFFFF',
        },
      },
      fontFamily: {
        headline: ['"Plus Jakarta Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        body: ['"Manrope"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      letterSpacing: {
        tightest: '-0.04em',
        tighter: '-0.02em',
      },
      backdropBlur: {
        glass: '24px',
      },
      boxShadow: {
        'ambient-sm': '0 4px 16px -4px rgba(15, 26, 24, 0.10)',
        'ambient-md': '0 12px 32px -8px rgba(15, 26, 24, 0.14)',
        'ambient-lg': '0 24px 48px -12px rgba(15, 26, 24, 0.18)',
      },
      keyframes: {
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-up': 'fadeUp 0.6s ease-out both',
      },
    },
  },
  plugins: [],
};

export default config;
