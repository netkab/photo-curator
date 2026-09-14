import {test} from 'node:test';
import assert from 'node:assert/strict';
const account='test@example.invalid';
const app='chrome-extension://'+ 'a'.repeat(32) + '/app/app.html';
async function setup(fail=false) {
  let listener, downloads=0, uploads=0;
  globalThis.chrome={
    runtime:{id:'a'.repeat(32),getURL:()=>app,onConnect:{addListener(){}},
      onMessage:{addListener(fn){listener=fn;}},onInstalled:{addListener(){}}},
    action:{onClicked:{addListener(){}}},
    storage:{local:{get:async()=>({'pc-token':'local-secret'})}},
    tabs:{query:async()=>[{id:1}]},
    scripting:{executeScript:async({args})=>{
      assert.ok(!JSON.stringify(args).includes('local-secret'));
      if(args[0]==='healthCheck') return [{result:{ok:true,result:{authed:true,account}}}];
      assert.equal(args[0],'fetchThumbnail');downloads++;
      return [{result:fail?{ok:false,error:'Google thumbnail HTTP 403'}:{ok:true,result:{data:'AQID'}}}];
    }},
  };
  globalThis.fetch=async(raw,options)=>{
    const url=new URL(raw);
    assert.equal(options.headers.Authorization,'Bearer local-secret');
    let result;
    if(url.pathname==='/api/direct/thumbnail-queue') {
      assert.equal(url.searchParams.get('limit'),'10');
      const after=Number(url.searchParams.get('after'));
      result={items:Array.from({length:12},(_,i)=>({media_id:i+1,account,url:'https://photos.fife.usercontent.google.com/fake'})).filter(i=>i.media_id>after).slice(0,10)};
    } else {assert.match(url.pathname,/^\/api\/direct\/thumbnails\/\d+$/);uploads++;result={cached:true};}
    return {ok:true,text:async()=>JSON.stringify(result)};
  };
  await import('../background/worker.js?test='+Math.random());
  const send=(args)=>new Promise(resolve=>listener({type:'PC_FETCH_THUMBNAILS',args},{id:chrome.runtime.id,url:app+'#sync'},resolve));
  return {send,counts:()=>({downloads,uploads})};
}
test('thumbnail batches return durable cursors and never pass local credentials to Google',async()=>{
  const {send,counts}=await setup();
  assert.equal((await send({after:-1})).ok,false);
  const first=await send({after:0});
  assert.equal(first.ok,true,first.error);assert.equal(first.cached,10);assert.equal(first.after,10);assert.equal(first.more,true);
  const second=await send({after:first.after});
  assert.equal(second.cached,2);assert.equal(second.after,12);
  assert.equal((await send({after:second.after})).more,false);
  assert.deepEqual(counts(),{downloads:12,uploads:12});
});
test('repeated Google download failures stop before attempting the whole batch',async()=>{
  const {send,counts}=await setup(true);
  const r=await send({after:0});
  assert.equal(r.ok,false);assert.match(r.error,/5 consecutive failures.*403/);
  assert.deepEqual(counts(),{downloads:5,uploads:0});
});
