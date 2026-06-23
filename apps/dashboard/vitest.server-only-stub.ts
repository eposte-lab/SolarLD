/**
 * Test stub for the `server-only` package.
 *
 * `server-only` deliberately throws when imported outside a React Server
 * Component bundle, to stop server code leaking to the client. Under vitest
 * (plain Node) that guard is irrelevant and would crash the import, so the
 * vitest config aliases `server-only` to this no-op module — letting pure
 * helpers inside `'server-only'`-guarded data files be unit-tested directly.
 */
export {};
