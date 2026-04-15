import type { Config } from 'tailwindcss';

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
      },
    },
  },
  plugins: [],
};

export default config;
