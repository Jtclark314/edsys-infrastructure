import React, {useLayoutEffect, useRef} from 'react';
import {AbsoluteFill,Composition,Sequence,registerRoot,useCurrentFrame,delayRender,continueRender,cancelRender,staticFile} from 'remotion';

export const states = ['idle','listening','thinking','speaking','greeting','error'] as const;
type State = typeof states[number];
type Point = [number,number];
const W=512,H=768,N=96,FPS=24;
const TAU=Math.PI*2;
const clamp=(n:number)=>Math.max(0,Math.min(1,n));
const smooth=(n:number)=>{const t=clamp(n);return t*t*(3-2*t);};
const bell=(x:number,y:number,cx:number,cy:number,rx:number,ry:number)=>smooth(1-((x-cx)/rx)**2-((y-cy)/ry)**2);

// A small continuous image-space puppet rig is needed here: generic whole-image
// transforms cannot articulate Phil's head and arm while keeping his feet still.
// Every joint is driven only by the Remotion frame; no wall clock/random/CSS loop.
function vertex(x:number,y:number,frame:number,state:State):Point {
  const t=frame/N,p=t*TAU;
  const breath=Math.sin(p), upper=smooth((1430-y)/1050);
  let dx=2.5*breath*upper,dy=-5*breath*upper;
  let head=0.025*Math.sin(p),nod=3*Math.sin(p);
  if(state==='listening'){head=0.065*Math.sin(p);nod=6*Math.sin(p*2);}
  if(state==='thinking'){head=0.045*Math.sin(p);nod=7*Math.sin(p);}
  if(state==='speaking'){head=0.060*Math.sin(p*2);nod=7*Math.sin(p*3);}
  if(state==='error'){head=0.085*Math.sin(p*2)*Math.sin(p/2)**2;nod=3*Math.sin(p);}
  const hw=smooth((490-y)/190)*smooth(1-((x-512)/430)**4),a=head;
  dx+=((x-512)*(Math.cos(a)-1)-(y-370)*Math.sin(a))*hw;
  dy+=((x-512)*Math.sin(a)+(y-370)*(Math.cos(a)-1))*hw+nod*hw;
  const arm=state==='greeting'?0.32*Math.sin(p*3)*Math.sin(p/2)**2:
    state==='speaking'?0.22*Math.sin(p*2):state==='error'?0.085*Math.sin(p):0;
  const raised=state==='greeting';
  // Keep the hair beside his head out of the larger forearm wave.
  const aw=smooth((x-(raised?740:645))/(raised?70:155))*smooth(((raised?680:820)-y)/160)*smooth((y-(raised?70:400))/130);
  const pivot:Point=raised?[780,520]:[695,600];
  dx+=((x-pivot[0])*(Math.cos(arm)-1)-(y-pivot[1])*Math.sin(arm))*aw;
  dy+=((x-pivot[0])*Math.sin(arm)+(y-pivot[1])*(Math.cos(arm)-1))*aw;
  // A slight independent sway of the hair tips; no silhouette-changing effects.
  dx+=4*Math.sin(p+0.8)*bell(x,y,335,340,210,210);
  if(state==='error')dy-=7*Math.sin(p)**2*bell(x,y,730,530,200,220);
  // Extra framing room keeps the larger gestures/head tilts inside the image.
  return [(512+(x+dx-512)*.96)/2,(1490+(y+dy-1490)*.96)/2];
}

function triangle(ctx:CanvasRenderingContext2D,img:CanvasImageSource,src:Point[],dst:Point[]) {
  const [s0,s1,s2]=src,[d0,d1,d2]=dst;
  const den=(s1[0]-s0[0])*(s2[1]-s0[1])-(s2[0]-s0[0])*(s1[1]-s0[1]);
  const a=((d1[0]-d0[0])*(s2[1]-s0[1])-(d2[0]-d0[0])*(s1[1]-s0[1]))/den;
  const c=((s1[0]-s0[0])*(d2[0]-d0[0])-(s2[0]-s0[0])*(d1[0]-d0[0]))/den;
  const b=((d1[1]-d0[1])*(s2[1]-s0[1])-(d2[1]-d0[1])*(s1[1]-s0[1]))/den;
  const d=((s1[0]-s0[0])*(d2[1]-d0[1])-(s2[0]-s0[0])*(d1[1]-d0[1]))/den;
  ctx.save();ctx.beginPath();
  const cx=(d0[0]+d1[0]+d2[0])/3,cy=(d0[1]+d1[1]+d2[1])/3;
  // Expand clips enough to fully cover subpixel antialiasing between adjacent
  // triangles. Neighboring triangles sample the same continuous source image.
  dst.forEach(([x,y],i)=>{const l=Math.hypot(x-cx,y-cy);const q:Point=[x+(x-cx)/l*2,y+(y-cy)/l*2];if(i)ctx.lineTo(...q);else ctx.moveTo(...q);});
  ctx.closePath();ctx.clip();ctx.setTransform(a,b,c,d,d0[0]-a*s0[0]-c*s0[1],d0[1]-b*s0[0]-d*s0[1]);
  ctx.drawImage(img,0,0,1024,1536);ctx.restore();
}

function patch(ctx:CanvasRenderingContext2D,img:HTMLImageElement,cx:number,cy:number,rx:number,ry:number,opacity:number){
  if(opacity<=0)return;
  const layer=document.createElement('canvas');layer.width=1024;layer.height=1536;
  const l=layer.getContext('2d')!;l.drawImage(img,0,0,1024,1536);
  const maskCanvas=document.createElement('canvas');maskCanvas.width=1024;maskCanvas.height=1536;
  const m=maskCanvas.getContext('2d')!;m.translate(cx,cy);m.scale(rx,ry);
  const mask=m.createRadialGradient(0,0,0.72,0,0,1);mask.addColorStop(0,'white');mask.addColorStop(1,'transparent');
  m.fillStyle=mask;m.fillRect(-1,-1,2,2);
  l.globalCompositeOperation='destination-in';l.drawImage(maskCanvas,0,0);
  ctx.globalAlpha=opacity;ctx.drawImage(layer,0,0);ctx.globalAlpha=1;
}

const Phil:React.FC<{state:State}>=({state})=>{
  const frame=useCurrentFrame(),ref=useRef<HTMLCanvasElement>(null);
  useLayoutEffect(()=>{
    const handle=delayRender('Draw Phil frame '+frame);
    let active=true;
    const names=[state==='speaking'?'idle':state,'blink','speaking'];
    Promise.all(names.map(name=>new Promise<HTMLImageElement>((resolve,reject)=>{
      const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('Missing Phil source '+name));image.src=staticFile('phil/'+name+'.png');
    }))).then(([base,blink,mouth])=>{
      if(!active)return;
      const source=document.createElement('canvas');source.width=1024;source.height=1536;
      const s=source.getContext('2d')!;s.drawImage(base,0,0,1024,1536);
      // Keep the same approved body during lip/blink changes; only masked face
      // regions use the identity-preserving image-generation edit frames.
      if(state==='idle'||state==='speaking'){
        const blinkAmount=[.35,.8,1,.8,.35][frame-68]||0;
        patch(s,blink,464,183,34,28,blinkAmount);patch(s,blink,533,183,34,28,blinkAmount);
      }
      if(state==='speaking'){
        const syllables=[0,.2,.55,.9,1,.7,.35,.1,0,.4,.8,1,.6,.25,0,0,.5,1,.8,.4,.1,0,.25,.5];
        patch(s,mouth,500,255,60,38,syllables[frame%syllables.length]);
      }
      // Generated inputs have a near-opaque (253/255) interior matte. Normalize
      // only that interior before overlapping mesh clips; retain soft hair alpha.
      const pixels=s.getImageData(0,0,1024,1536);
      for(let i=3;i<pixels.data.length;i+=4)if(pixels.data[i]>=248)pixels.data[i]=255;
      s.putImageData(pixels,0,0);
      const ctx=ref.current!.getContext('2d')!;ctx.clearRect(0,0,W,H);ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';
      const cols=24,rows=36;
      for(let row=0;row<rows;row++)for(let col=0;col<cols;col++){
        const x=col*1024/cols,y=row*1536/rows,x1=(col+1)*1024/cols,y1=(row+1)*1536/rows;
        const a:Point=[x,y],b:Point=[x1,y],c:Point=[x1,y1],d:Point=[x,y1];
        const av=vertex(...a,frame,state),bv=vertex(...b,frame,state),cv=vertex(...c,frame,state),dv=vertex(...d,frame,state);
        triangle(ctx,source,[a,b,c],[av,bv,cv]);triangle(ctx,source,[a,c,d],[av,cv,dv]);
      }
      continueRender(handle);
    }).catch(cancelRender);
    return ()=>{active=false;continueRender(handle);};
  },[frame,state]);
  return <canvas ref={ref} width={W} height={H} style={{width:W,height:H}}/>;
};
const descriptions:Record<State,string>={idle:'Front-facing eye contact, breathing and blinking',listening:'A clear listening tilt and attentive nod',thinking:'A thoughtful chin gesture and tablet glance',speaking:'Expressive nods, mouth movement and hand gestures',greeting:'A bigger, friendly wave',error:'A concerned head shake and gentle shrug'};
const Review=()=> <AbsoluteFill style={{background:'#111b24',color:'#ece3d4',fontFamily:'sans-serif'}}>
  {states.map((state,index)=><Sequence key={state} from={index*N} durationInFrames={N}>
    <div style={{position:'absolute',left:160,top:80,scale:'1.2',transformOrigin:'top left'}}><Phil state={state}/></div>
    <div style={{position:'absolute',left:960,top:330,width:780}}>
      <div style={{fontSize:26,color:'#d4ad65',letterSpacing:4}}>DIMENSIONAL PHIL</div>
      <div style={{fontSize:78,marginTop:28,textTransform:'capitalize'}}>{state}</div>
      <div style={{fontSize:31,marginTop:28,lineHeight:1.5,color:'#b9c4c9'}}>{descriptions[state]}</div>
      <div style={{fontSize:24,marginTop:80,color:'#82939d'}}>Animation preview · silent<br/>Voice timing remains a separate integration</div>
    </div>
  </Sequence>)}
</AbsoluteFill>;
const Root=()=> <>{states.map(state=><Composition key={state} id={'Phil-'+state} component={Phil} defaultProps={{state}} width={W} height={H} fps={FPS} durationInFrames={N}/>)}<Composition id="Phil-Review" component={Review} width={1920} height={1080} fps={FPS} durationInFrames={N*6}/></>;
registerRoot(Root);
