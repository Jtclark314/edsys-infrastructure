import {bundle} from '@remotion/bundler';
import {renderMedia,selectComposition} from '@remotion/renderer';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=dirname(fileURLToPath(import.meta.url));
const provenance=JSON.parse(await readFile(resolve(root,'source-prompts.json'),'utf8'));
for(const [name,sha] of Object.entries(provenance.source_sha256)){
  if(createHash('sha256').update(await readFile(resolve(root,'public/phil',name))).digest('hex')!==sha)throw Error('Restore the approved Phil source pose: '+name);
}
execFileSync('findmnt',['-M','/mnt/ai-store'],{stdio:'ignore'});
const serveUrl=await bundle({entryPoint:resolve(root,'src/index.tsx'),publicDir:resolve(root,'public'),outDir:'/mnt/ai-store/remotion/tmp/bedroom-phil-review-bundle'});
const composition=await selectComposition({serveUrl,id:'Phil-Review'});
const directory='/mnt/ai-store/remotion/outputs/bedroom-phil';
await renderMedia({serveUrl,composition,codec:'h264',hardwareAcceleration:'required',videoBitrate:'8M',pixelFormat:'yuv420p',imageFormat:'png',colorSpace:'bt709',concurrency:6,outputLocation:directory+'/review-raw.mp4',chromiumOptions:{gl:null}});
execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-i',directory+'/review-raw.mp4','-map','0:v:0','-an','-c','copy','-bsf:v','h264_metadata=video_full_range_flag=0:colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1','-movflags','+faststart',directory+'/phil-animation-review.mp4']);
console.log(directory+'/phil-animation-review.mp4');
