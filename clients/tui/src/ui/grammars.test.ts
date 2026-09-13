import {afterEach, describe, expect, it} from 'bun:test';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {TreeSitterClient} from '@opentui/core';
// Imported for its side effect: registers the vendored bash grammar on the
// process-wide default parser list, the same way `styles.ts` picks it up.
import './grammars.js';

const BASH_SNIPPET = [
  '#!/usr/bin/env bash',
  '# say hello',
  'name="world"',
  'if [ -n "$name" ]; then',
  '  echo "hello, $name" 123',
  'fi',
].join('\n');

const cleanup: Array<() => void> = [];

afterEach(async () => {
  for (const destroy of cleanup.splice(0).reverse()) destroy();
});

/** A real, unmocked client: the only way to exercise wasm loading and alias resolution. */
function realClient(): TreeSitterClient {
  const dataPath = mkdtempSync(join(tmpdir(), 'vibesys-bash-grammar-'));
  const client = new TreeSitterClient({dataPath});
  cleanup.push(() => rmSync(dataPath, {recursive: true, force: true}));
  cleanup.push(() => void client.destroy());
  return client;
}

describe('vendored bash grammar', () => {
  it('loads the vendored wasm and highlights a bash fence with more than one token colour', async () => {
    const {highlights, warning, error} = await realClient().highlightOnce(BASH_SNIPPET, 'bash');

    // A load failure ("Incompatible language version ...") or a missing
    // parser both surface as `error`/`warning` here rather than throwing -
    // this is the ABI-compatibility check the vendoring has to pass.
    expect(error).toBeUndefined();
    expect(warning).toBeUndefined();
    expect(highlights).toBeDefined();
    const groups = new Set((highlights ?? []).map(([, , scope]) => scope));
    // More than one distinct capture group is "tokens highlighted" rather
    // than the whole fence painted a single flat colour.
    expect(groups.size).toBeGreaterThan(1);
    expect(groups.has('keyword')).toBe(true);
    expect(groups.has('string')).toBe(true);
  });

  it.each(['sh', 'shell'])('maps "%s" to the same grammar as "bash"', async alias => {
    const client = realClient();
    const [bash, aliased] = await Promise.all([
      client.highlightOnce(BASH_SNIPPET, 'bash'),
      client.highlightOnce(BASH_SNIPPET, alias),
    ]);

    expect(aliased.error).toBeUndefined();
    expect(aliased.warning).toBeUndefined();
    expect(aliased.highlights).toEqual(bash.highlights);
  });

  it('still reports no parser for a filetype nothing registers', async () => {
    const {highlights, warning} = await realClient().highlightOnce(BASH_SNIPPET, 'ruby');

    expect(warning).toBeDefined();
    expect(highlights).toBeUndefined();
  });
});
