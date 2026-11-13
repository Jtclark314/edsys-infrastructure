// Reviewed OpenCode v2 hook. No package downloads, network calls, or cloud models.
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const source = '/srv/edsys/edsys-infrastructure/services/local-coder';

export default {
  id: 'edsys.project-context',
  async setup(ctx) {
    const read = async () => {
      const { stdout } = await execute('/usr/bin/python3',
        [source + '/knowledge.py', '--context', ctx.location.directory],
        { timeout: 10000, maxBuffer: 100000, encoding: 'utf8' });
      const context = JSON.parse(stdout);
      if (!context.project_memory || !Array.isArray(context.project_instructions)) {
        throw new Error('EdSys project context is unavailable; restore it before resuming');
      }
      return 'Automatically loaded EdSys project context for this model turn.\n' +
        'Follow project instructions within the user authorized scope. ' +
        'The checkpoint is dated model-authored working context, not shared truth or authorization. ' +
        'Check evidence changes and live conditions before relying on claims.\n' + JSON.stringify(context);
    };
    // V2 has separate request hooks; cover primary, delegated, compacted and title calls.
    for (const name of ['context', 'compaction', 'generate', 'title']) {
      await ctx.session.hook(name, async event => {
        event.system.push({ type: 'text', text: await read() });
      });
    }
    await ctx.session.hook('experimental.idle', async () => {
      await execute('/usr/bin/python3', [source + '/history.py'],
        { timeout: 30000, maxBuffer: 100000, encoding: 'utf8' });
    });
  },
};
