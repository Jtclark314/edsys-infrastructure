'use strict';
const byId = id => document.getElementById(id);
let scenes = [], index = 0, deadline = 0, dismissed = false;
const labels = {listening:'Listening',thinking:'Processing','reply-pending':'Reply ready',speaking:'Speaking',camera:'Camera',morning:'Morning',bedtime:'Bedtime',answer:'Answer',notification:'Notification',media:'Media',ambient:'Ambient',result:'Clarification',failed:'Failure'};
function dismiss() {
  dismissed = true;
  byId('overlay').hidden = true;
  byId('overlay').style.display = 'none';
  byId('dismissed').hidden = false;
}
function selectScene(next) {
  index = (next + scenes.length) % scenes.length;
  const s = scenes[index];
  dismissed = false;
  deadline = performance.now() + (s.ttl_seconds || 20) * 1000;
  byId('overlay').hidden = false;
  byId('overlay').style.display = '';
  byId('dismissed').hidden = true;
  byId('overlay').className = 'overlay ' + (s.state || 'idle');
  for (const key of ['heading','body','kind']) byId(key).textContent = s[key];
  byId('state').textContent = (s.state || 'idle').replaceAll('_',' ');
  byId('items').replaceChildren(...(s.items || []).map(text => {const li=document.createElement('li');li.textContent=text;return li;}));
  byId('audio-status').textContent = s.audio_playing ? 'Simulated playback acknowledgement · silent demo' : 'No reply audio playing';
  [...byId('scenes').children].forEach((b,i) => b.setAttribute('aria-pressed',String(i===index)));
  tick();
}
function tick() {
  if (!scenes.length || dismissed) return;
  const s = scenes[index], remaining = deadline - performance.now();
  if (remaining <= 0) {dismiss();return;}
  let pose = 'idle';
  if (s.state === 'listening') pose = 'listening';
  if (s.state === 'processing') pose = 'thinking';
  if (s.state === 'failed') pose = 'failed';
  if (s.state === 'responding' && s.audio_playing === true) pose = 'speaking-' + (Math.floor(performance.now()/200)%2);
  const image = '/butler-' + pose + '.png';
  if (byId('butler').getAttribute('src') !== image) byId('butler').src = image;
  byId('expiry').textContent = 'Dismisses in ' + Math.ceil(remaining/1000) + 's';
}
byId('dismiss').addEventListener('click',dismiss);
document.addEventListener('keydown',e => {
  if (!scenes.length) return;
  if (e.key==='Escape') {e.preventDefault();dismiss();}
  if (e.key==='ArrowRight') {e.preventDefault();selectScene(index+1);}
  if (e.key==='ArrowLeft') {e.preventDefault();selectScene(index-1);}
});
fetch('/views.json').then(r=>{if(!r.ok)throw Error('fixtures unavailable');return r.json();}).then(data=>{
  scenes=data;
  scenes.forEach((s,i)=>{const b=document.createElement('button');b.type='button';b.textContent=labels[s.key];b.addEventListener('click',()=>selectScene(i));byId('scenes').append(b);});
  selectScene(0);
}).catch(()=>{byId('heading').textContent='Preview unavailable';byId('body').textContent='Synthetic fixtures could not be loaded.';});
setInterval(tick,200);
