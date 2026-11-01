import { readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
const paths = [];
function walk(dir) {
  for (const item of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, item.name);
    if (item.isDirectory()) walk(path);
    else paths.push(path);
  }
}
walk(root);
const maps = paths.filter(path => path.endsWith('.map'));
if (!maps.length) throw new Error('No source maps: cannot audit the selected build flavor.');
const sources = new Set();
for (const path of maps) {
  for (const source of JSON.parse(readFileSync(path, 'utf8')).sources ?? []) sources.add(source);
}
const excluded = /(?:^|\/)src\/(?:processor\/|editor\/(?:proprietary|desktop|saas|cloud)\/)/;
const forbidden = [...sources].filter(source => excluded.test(source));
if (forbidden.length) throw new Error(`Non-core code in build: ${forbidden.join(', ')}`);
if (![...sources].some(source => source.includes('/src/editor/core/'))) {
  throw new Error('Core editor sources were not found.');
}
writeFileSync(join(root, 'edsys-core-build.json'), JSON.stringify({
  version: '3.2.2', flavor: 'core', processor: false,
  sourceMapsAudited: maps.length, sourcesAudited: sources.size,
  proprietarySources: forbidden.length,
}) + '\n');
// Do not ship source maps or source content in the deployed image.
for (const path of paths.filter(path => /\.map(?:\.(?:br|gz))?$/.test(path))) unlinkSync(path);
console.log(`Core-only audit passed: ${sources.size} sources in ${maps.length} maps.`);
