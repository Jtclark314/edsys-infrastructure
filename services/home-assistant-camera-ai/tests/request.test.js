import test from 'node:test';
import assert from 'node:assert/strict';
import {CameraAIRequest} from '../camera-ai-request.js';
const input = {provider:'fixture-provider',camera:'camera.outdoor',question:'What is visible?',allowedCameras:['camera.outdoor']};
const states = {'camera.outdoor':{state:'idle',attributes:{friendly_name:'Outdoor'}}};
const response = {response:{response_text:'A quiet driveway.\n'+ 'More detail. '.repeat(60),key_frame:'/media/llmvision/snapshots/fixture.jpg'}};

test('one paid request; full question/answer saved locally; no generated-title request', async () => {
  const calls=[];const request=new CameraAIRequest();
  assert.equal(await request.run({states,callWS:async call=>{calls.push(call);return call.service==='image_analyzer'?response:null;}},input),true);
  assert.equal(calls.length,2);
  assert.equal(calls[0].service_data.store_in_timeline,false);
  assert.equal(calls[0].service_data.generate_title,false);
  assert.equal(calls[0].service_data.max_tokens,500);
  assert.equal(calls[1].service,'create_event');
  assert.ok(calls[1].service_data.description.includes(response.response.response_text));
  assert.equal(calls[1].service_data.camera_entity,input.camera);
  assert.equal(request.result.saved,true);
  assert.equal(request.busy,false);
});

test('double activation while waiting cannot send a second analysis',async()=>{
  let complete;let calls=0;
  const hass={states,callWS:async call=>{calls++;return call.service==='image_analyzer'?await new Promise(r=>complete=r):null;}};
  const request=new CameraAIRequest();const first=request.run(hass,input);
  assert.equal(request.busy,true);assert.equal(await request.run(hass,input),false);
  assert.equal(calls,1);complete(response);await first;assert.equal(calls,2);
});

test('unavailable and out-of-scope cameras and blank questions cannot send requests',async()=>{
  let calls=0;const hass={states,callWS:async()=>calls++};const request=new CameraAIRequest();
  await request.run(hass,{...input,camera:'camera.other'});
  await request.run(hass,{...input,question:'   '});
  await request.run({...hass,states:{'camera.outdoor':{state:'unavailable',attributes:{}}}},input);
  assert.equal(calls,0);
});

test('provider failure preserves previous answer and does not retry or save',async()=>{
  let calls=0;const request=new CameraAIRequest();request.result={answer:'Earlier answer',saved:true};
  assert.equal(await request.run({states,callWS:async()=>{calls++;throw {code:'service_validation_error',message:'private backend details'};}},input),false);
  assert.equal(calls,1);assert.equal(request.result.answer,'Earlier answer');assert.equal(request.busy,false);
  assert.ok(!request.error.includes('private backend'));
});

test('local history failure retains the paid answer and explicitly reports uncertainty',async()=>{
  let calls=0;const request=new CameraAIRequest();
  await request.run({states,callWS:async call=>{calls++;if(call.service==='create_event')throw new Error();return response;}},input);
  assert.equal(calls,2);assert.equal(request.result.answer,response.response.response_text);
  assert.equal(request.result.saved,false);assert.match(request.error,/history could not be confirmed/);
});

test('empty provider answer is rejected without a timeline event',async()=>{
  let calls=0;const request=new CameraAIRequest();
  await request.run({states,callWS:async()=>{calls++;return {response:{response_text:' '}};}},input);
  assert.equal(calls,1);assert.equal(request.result,null);assert.equal(request.busy,false);
});
