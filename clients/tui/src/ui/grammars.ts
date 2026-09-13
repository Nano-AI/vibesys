import {fileURLToPath} from 'node:url';
import {addDefaultParsers} from '@opentui/core';

/**
 * Vendors tree-sitter-bash so fenced ```bash / ```sh / ```shell blocks (and
 * any `filetype: 'bash'` block built outside markdown) highlight instead of
 * falling back to flat text.
 *
 * `@opentui/core` ships five grammars of its own
 * (`node_modules/@opentui/core/assets/`); bash is not one of them, and
 * vendoring the rest of issue #575's languages (C++, Rust, Go, Python) was
 * measured and declined for bundle size (see `styles.ts`'s `GRAMMAR_FILETYPES`
 * doc comment). Bash is the one exception: it is highlighted on its own
 * merits, not as a reopening of that decision.
 *
 * Source: `tree-sitter-bash@0.25.1` from the npm registry
 * (https://registry.npmjs.org/tree-sitter-bash/-/tree-sitter-bash-0.25.1.tgz),
 * which ships a prebuilt `tree-sitter-bash.wasm` and `queries/highlights.scm`
 * at its package root - the same two files the parked vendoring script pulls
 * for C++, Rust, Go and Python, and nothing else from the tarball (no
 * `prebuilds/*.node`, no `src/parser.c`). SHA-256, as vendored into
 * `clients/tui/assets/bash/`:
 *   tree-sitter-bash.wasm  8292919c88a0f7d3fb31d0cd0253ca5a9531bc1ede82b0537f2c63dd8abe6a7a
 *   highlights.scm         b74220d954f485b7626d2b2b61f37b522e12eb1830803e388e57dd797dc99f11
 * `LICENSE` (MIT, Max Brunsfeld) sits alongside them.
 *
 * ABI compatibility is not assumed: this grammar was built against
 * `tree-sitter-cli ^0.25.6` (language ABI 15), and `grammars.test.ts` loads
 * the vendored `.wasm` through the exact `web-tree-sitter` version
 * `@opentui/core` depends on (0.25.10) to prove it parses rather than
 * throwing "Incompatible language version".
 *
 * `addDefaultParsers` is `@opentui/core`'s own extension point for this: it
 * overrides `getTreeSitterClient()`'s default parser list before that
 * singleton's next `initialize()`, which is how `CodeRenderable` picks up a
 * client when nothing more specific is passed to it (`styles.ts` never sets
 * one). Importing this module for its side effect - `styles.ts` does, at the
 * top - has to happen before anything triggers that `initialize()`, which in
 * practice means before the first render.
 *
 * `aliases: ['sh', 'shell']` registers both other spellings under the same
 * grammar. In practice a ```sh fence never reaches this as literal "sh":
 * `@opentui/core`'s own `extensionToFiletype` table already resolves the
 * "sh" info string to "bash" before a block is built, so the alias mostly
 * documents intent and covers any caller that passes "sh" straight to
 * `highlightOnce`/`createBuffer`. "shell" has no such built-in mapping and
 * does reach the client as a literal filetype, so its alias is load-bearing.
 * `styles.ts`'s `GRAMMAR_FILETYPES` set has to name "bash" and "shell" too:
 * that set gates whether the renderer treats a block as having a shipped
 * grammar at all, and it does not know about worker-side aliases.
 */
const ASSETS_DIR = new URL('../../assets/bash/', import.meta.url);

export const BASH_GRAMMAR_WASM = fileURLToPath(new URL('tree-sitter-bash.wasm', ASSETS_DIR));
export const BASH_GRAMMAR_HIGHLIGHTS = fileURLToPath(new URL('highlights.scm', ASSETS_DIR));

addDefaultParsers([
  {
    filetype: 'bash',
    aliases: ['sh', 'shell'],
    wasm: BASH_GRAMMAR_WASM,
    queries: {highlights: [BASH_GRAMMAR_HIGHLIGHTS]},
  },
]);
