import {test, beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {pageCommand} from '../main/commands.js';
import {readFileSync} from 'node:fs';
const account='test@example.invalid';
let calls;
beforeEach(() => {
  globalThis.window={WIZ_global_data:{oPEP7c:account, SNlM0e:'fake-csrf',FdrFJe:'session',cfb2h:'build',eptZe:'/_/PhotosUi/'}};
  globalThis.location={pathname:'/u/1/photos',href:'https://photos.google.com/u/1/photos'};
  calls=[];
  globalThis.fetch=async (url, opts) => {
    calls.push({url,opts});
    const id=new URL(url).searchParams.get('rpcids');
    return {ok:true,text:async()=>JSON.stringify([['wrb.fr',id,JSON.stringify([null])]])};
  };
});
test('health uses stable identity, fails closed without it',async()=>{
  assert.equal((await pageCommand('healthCheck')).result.account,account);
  delete window.WIZ_global_data.oPEP7c;
  assert.equal((await pageCommand('healthCheck')).result.authed,false);
});
test('every mutation defaults to dry run and sends no request',async()=>{
  for(const name of ['trash','restore']) {
    const r=await pageCommand(name,{keys:['content-1'],expectedAccount:account});
    assert.equal(r.result.dryRun,true);
  }
  assert.equal(calls.length,0);
});
test('explicit live trash and restore use only pinned reversible RPC shapes',async()=>{
  for(const [name,payload] of [['trash',[null,1,['k'],3]],['restore',[null,3,['k'],2]]]) {
    assert.equal((await pageCommand(name,{keys:['k'],expectedAccount:account,dryRun:false})).ok,true);
    const call=calls.at(-1);
    const wrapper=JSON.parse(call.opts.body.get('f.req'));
    assert.equal(wrapper[0][0][0],'XwAOJf');
    assert.deepEqual(JSON.parse(wrapper[0][0][1]),payload);
    assert.equal(new URL(call.url).pathname,'/_/PhotosUi/data/batchexecute');
  }
});
test('scan uses the Google-supplied service root for default and multi-account pages',async()=>{
  for (const path of ['/_/PhotosUi/', '/u/1/_/PhotosUi/']) {
    window.WIZ_global_data.eptZe=path;
    const r=await pageCommand('scanPage',{expectedAccount:account});
    assert.equal(r.ok,true,r.error);
    const url=new URL(calls.at(-1).url);
    assert.equal(url.origin,'https://photos.google.com');
    assert.equal(url.pathname,path+'data/batchexecute');
    assert.equal(url.searchParams.get('source-path'),'/u/1/photos');
  }
});
test('missing or unsafe service roots fail before sending credentials',async()=>{
  for(const path of [undefined,'/u/1/','https://evil.invalid/','//evil.invalid/','/_/PhotosUi/../','/_/PhotosUi/?secret=']) {
    window.WIZ_global_data.eptZe=path;
    const r=await pageCommand('scanPage',{expectedAccount:account});
    assert.equal(r.ok,false);
    assert.match(r.error,/API path unavailable or changed/);
  }
  assert.equal(calls.length,0);
});
test('HTTP failures name the failing RPC and endpoint without leaking credentials',async()=>{
  globalThis.fetch=async()=>({ok:false,status:405});
  const r=await pageCommand('scanPage',{expectedAccount:account});
  assert.equal(r.ok,false);
  assert.match(r.error,/HTTP 405 \(lcxiM at \/_\/PhotosUi\/data\/batchexecute\)/);
  assert.ok(!r.error.includes('fake-csrf'));
  assert.ok(!r.error.includes(account));
});
test('account changes, unsupported commands, and oversized batches send no RPC',async()=>{
  for(const [name,args] of [
    ['trash',{keys:['k'],expectedAccount:'other',dryRun:false}],
    ['permanent_delete',{keys:['k'],expectedAccount:account,dryRun:false}],
    ['trash',{keys:Array(26).fill('k'),expectedAccount:account,dryRun:false}],
    ['empty_trash',{expectedAccount:account}],
  ]) assert.equal((await pageCommand(name,args)).ok,false);
  assert.equal(calls.length,0);
});
test('malformed mutation response is a failure, never reported as succeeded',async()=>{
  globalThis.fetch=async()=>({ok:true,text:async()=>'<html>signed out</html>'});
  const r=await pageCommand('trash',{keys:['k'],expectedAccount:account,dryRun:false});
  assert.equal(r.ok,false);assert.match(r.error,/schema changed/);
});
test('scan parses metadata, ownership, video duration, and cursor',async()=>{
  const item=Array(16).fill(null);
  item[0]='media-1';item[1]=['https://lh3.googleusercontent.com/x',4000,3000];
  item[2]=1700000000000;item[3]='dedup-1';item[7]=[];item[15]={76647426:[3000]};
  const info=Array(31).fill(null);info[0]='media-1';info[2]='photo.jpg';info[5]=12345;
  globalThis.fetch=async(url,opts)=>{
    calls.push({url,opts});
    const id=new URL(url).searchParams.get('rpcids');
    const data=id==='lcxiM'?[[item],'next-cursor']: [[null,[[info]]]];
    return {ok:true,text:async()=>JSON.stringify([['wrb.fr',id,JSON.stringify(data)]])};
  };
  const r=await pageCommand('scanPage',{expectedAccount:account});
  assert.equal(r.ok,true,r.error);
  const m=r.result.items[0];
  assert.equal(m.file_name,'photo.jpg');assert.equal(m.bytes,12345);
  assert.equal(m.duration,3);assert.equal(m.is_owned,true);
  assert.equal(r.result.nextPageId,'next-cursor');
  const metadata=JSON.parse(JSON.parse(calls[1].opts.body.get('f.req'))[0][0][1]);
  assert.deepEqual(metadata[0][0],[[['media-1']]]);
  assert.equal(metadata[0][1][0].length,37);
});
test('extension has no generic page message bridge or external connections',()=>{
  const manifest=JSON.parse(readFileSync(new URL('../manifest.json',import.meta.url)));
  assert.equal(manifest.content_scripts,undefined);
  assert.equal(manifest.externally_connectable,undefined);
  assert.equal(manifest.web_accessible_resources,undefined);
  assert.deepEqual(manifest.host_permissions,['https://photos.google.com/*','http://localhost:8077/*']);
  const worker=readFileSync(new URL('../background/worker.js',import.meta.url),'utf8');
  assert.ok(!worker.includes('PC_GP_CALL'));
  assert.ok(!worker.includes('window.postMessage'));
});
