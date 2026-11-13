import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import plugin from '../context-plugin-v2/index.js';

test('v2 context covers primary, delegated, title, generate and compaction requests', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'edsys-v2-context-'));
  const hooks = new Map();
  try {
    await writeFile(join(dir, 'AGENTS.md'), 'V2-CONTEXT-FIRST-4829');
    await plugin.setup({location: {directory: dir}, session: {
      async hook(name, callback) {hooks.set(name, callback);},
    }});
    assert.deepEqual([...hooks.keys()], ['context','compaction','generate','title','experimental.idle']);
    for (const name of ['context','compaction','generate','title']) {
      const event = {sessionID: 'primary-or-child', system: [{type:'text',text:'existing'}]};
      await hooks.get(name)(event);
      assert.equal(event.system[0].text, 'existing');
      assert.match(event.system[1].text, /V2-CONTEXT-FIRST-4829/);
      assert.match(event.system[1].text, /model-authored/);
    }
    await writeFile(join(dir, 'AGENTS.md'), 'V2-CONTEXT-UPDATED-4829');
    const event = {system: []};
    await hooks.get('context')(event);
    assert.match(event.system[0].text, /V2-CONTEXT-UPDATED-4829/);
    assert.doesNotMatch(event.system[0].text, /V2-CONTEXT-FIRST-4829/);
  } finally {await rm(dir, {recursive:true,force:true});}
});

test('v2 refuses a model call if automatic project context cannot be read', async () => {
  const hooks = new Map();
  await plugin.setup({location:{directory:'/nonexistent/edsys-v2-fixture'},session:{
    async hook(name, callback) {hooks.set(name,callback);},
  }});
  await assert.rejects(hooks.get('context')({system:[]}));
});
