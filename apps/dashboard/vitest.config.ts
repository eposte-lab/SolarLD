import { defineConfig } from 'vitest/config';
import path from 'node:path';

/**
 * Vitest config for apps/dashboard.
 *
 * We run tests in jsdom so React components that touch the DOM (the
 * realtime toaster, bento card helpers) can render under @testing-library.
 * Path alias `@/` mirrors tsconfig.json so imports match production code.
 */
export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    css: false,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      // `server-only` throws on import outside an RSC bundle; no-op it in tests
      // so pure helpers in server-guarded data modules stay unit-testable.
      'server-only': path.resolve(__dirname, './vitest.server-only-stub.ts'),
    },
  },
});
