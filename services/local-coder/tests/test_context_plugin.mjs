import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { EdSysProjectContext } from '../context-plugin.mjs';

test('automatic context reaches every turn and compaction, and observes guidance changes', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'edsys-context-test-'));
  try {
    await writeFile(join(dir, 'AGENTS.md'), 'Project marker: HOOK-FIRST-4829');
    const hooks = await EdSysProjectContext({ directory: dir });
    const first = { system: ['existing'] };
    await hooks['experimental.chat.system.transform']({}, first);
    assert.equal(first.system[0], 'existing');
    assert.match(first.system[1], /HOOK-FIRST-4829/);
    assert.match(first.system[1], /model-authored/);
    await writeFile(join(dir, 'AGENTS.md'), 'Project marker: HOOK-UPDATED-4829');
    const next = { system: [] };
    await hooks['experimental.chat.system.transform']({ sessionID: 'child' }, next);
    assert.match(next.system[0], /HOOK-UPDATED-4829/);
    assert.doesNotMatch(next.system[0], /HOOK-FIRST-4829/);
    const compact = { context: ['existing'] };
    await hooks['experimental.session.compacting']({}, compact);
    assert.equal(compact.context[0], 'existing');
    assert.match(compact.context[1], /HOOK-UPDATED-4829/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('unreadable project context fails instead of silently dropping memory', async () => {
  const hooks = await EdSysProjectContext({ directory: '/nonexistent/edsys-context-fixture' });
  await assert.rejects(hooks['experimental.chat.system.transform']({}, { system: [] }));
});
