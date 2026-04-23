/**
 * CSS side-effect import declarations.
 *
 * TypeScript 5.9+ enables `noUncheckedSideEffectImports` as part of `strict`,
 * which requires that every side-effect import (`import './foo.css'`) is
 * covered by a matching module declaration. Next.js provides `*.module.css`
 * but not plain `*.css`, so we add it here.
 *
 * This does NOT affect CSS Modules (*.module.css) — those are handled by
 * the `/// <reference types="next" />` in next-env.d.ts which maps them to
 * `{ readonly [key: string]: string }`.
 */

// Plain CSS files imported for side-effects (e.g. globals.css)
declare module '*.css' {}
