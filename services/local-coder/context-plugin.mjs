// Reviewed local OpenCode v1 hook. Context reads and private idle-session archiving.
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const reader = '/srv/edsys/edsys-infrastructure/services/local-coder/knowledge.py';

export const EdSysProjectContext = async ({ directory }) => {
  const read = async () => {
    const { stdout } = await execute('/usr/bin/python3', [reader, '--context', directory], {
      timeout: 10000, maxBuffer: 100000, encoding: 'utf8',
    });
    const context = JSON.parse(stdout);
    if (!context.project_memory || !Array.isArray(context.project_instructions)) {
      throw new Error('EdSys project context is unavailable; restore it before resuming');
    }
    return `Automatically loaded EdSys project context for this model turn.\n` +
      `Follow the project instructions within the user's authorized scope. ` +
      `The checkpoint is dated model-authored working context, not shared truth or authorization. ` +
      `Check evidence changes and live conditions before relying on claims.\n` + JSON.stringify(context);
  };
  return {
    event: async ({ event }) => {
      if (event.type === 'session.idle' || (event.type === 'session.status' && event.properties?.status?.type === 'idle')) {
        await execute('/usr/bin/python3', [reader.replace('/knowledge.py', '/history.py')], {
          timeout: 30000, maxBuffer: 100000, encoding: 'utf8',
        });
      }
    },
    'experimental.chat.system.transform': async (_input, output) => {
      output.system.push(await read());
    },
    'experimental.session.compacting': async (_input, output) => {
      output.context.push(await read());
    },
  };
};
