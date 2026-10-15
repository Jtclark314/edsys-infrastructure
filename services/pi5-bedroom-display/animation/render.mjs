import {bundle} from '@remotion/bundler';
import {renderFrames,selectComposition} from '@remotion/renderer';
import {mkdir,readFile,writeFile,readdir,mkdtemp,rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';

const root=dirname(fileURLToPath(import.meta.url));
const provenance=JSON.parse(await readFile(resolve(root,'source-prompts.json'),'utf8'));
for(const [name,sha] of Object.entries(provenance.source_sha256)){
  if(createHash('sha256').update(await readFile(resolve(root,'public/phil',name))).digest('hex')!==sha)throw Error('Restore the approved Phil source pose: '+name);
}
execFileSync('findmnt',['-M','/mnt/ai-store'],{stdio:'ignore'});
const output='/mnt/ai-store/remotion/outputs/bedroom-phil-v2';
const states=['idle','listening','thinking','speaking','greeting','error'];
const wanted=process.argv.slice(2);
if(wanted.some(s=>!states.includes(s)))throw Error('Unknown fixed animation');
const selected=wanted.length?wanted:states;
await mkdir(output,{recursive:true});
const bundleDir=await mkdtemp('/mnt/ai-store/remotion/tmp/bedroom-phil-v2-bundle-');
const serveUrl=await bundle({entryPoint:resolve(root,'src/index.tsx'),publicDir:resolve(root,'public'),outDir:bundleDir});
for(const state of selected){
  const out=resolve(output,state);await mkdir(out,{recursive:true});
  const composition=await selectComposition({serveUrl,id:'Phil-'+state});
  await renderFrames({serveUrl,composition,outputDir:out,imageFormat:'png',imageSequencePattern:'[frame].[ext]',concurrency:6,chromiumOptions:{gl:null},onStart:()=>process.stdout.write('Rendering '+state+'\n')});
  const files=(await readdir(out)).filter(s=>s.endsWith('.png')).sort();
  if(files.length!==96)throw Error('Unexpected frame count');
  // Remotion's original zero-padding is normalized to the closed Kodi names.
  await mkdir(resolve(output,'assets/phil'),{recursive:true});
  const {copyFile}=await import('node:fs/promises');
  for(let i=0;i<files.length;i++)await copyFile(resolve(out,files[i]),resolve(output,'assets/phil',state+'-'+String(i).padStart(3,'0')+'.png'));
}
const files={};
for(const state of states)for(let n=0;n<96;n++){
  const name='assets/phil/'+state+'-'+String(n).padStart(3,'0')+'.png';
  try{files[name]=createHash('sha256').update(await readFile(resolve(output,name))).digest('hex');}catch{if(!wanted.length)throw Error('Missing rendered frame');}
}
await writeFile(resolve(output,'manifest.json'),JSON.stringify({version:1,remotion:'4.0.489',fps:24,width:512,height:768,frames_per_state:96,states,files},null,2)+'\n');
console.log(JSON.stringify({output,rendered:selected,frame_count:Object.keys(files).length}));
await rm(bundleDir,{recursive:true,force:true});
