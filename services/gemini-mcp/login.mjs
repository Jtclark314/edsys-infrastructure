import { spawn } from 'node:child_process';
import { childEnv, cli, work, checkPolicy } from './runner.mjs';
try { checkPolicy(); } catch (error) {
  console.error(`Google AI login could not start: ${error.message}`);
  process.exit(1);
}
process.stdout.write('EdSys Google AI: sign in to Antigravity with your Google AI Pro account.\nPaste any browser authorization code into this terminal, never chat.\nAfter sign-in, enter /usage to check your plan, then /quit.\n');
const child = spawn(cli, [], {
  cwd: work, env: childEnv(true), stdio: 'inherit',
});
child.on('exit', code => process.exit(code ?? 1));
child.on('error', () => { console.error('Could not launch Antigravity CLI.'); process.exit(1); });
