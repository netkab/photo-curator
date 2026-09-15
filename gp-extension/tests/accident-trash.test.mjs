import {test} from 'node:test';
import assert from 'node:assert/strict';
import {trashAccident} from '../lib/accident-trash.js';
function setup(dryRun, accepted=true) {
  const calls=[];
  const args={dryRun, group:{id:1,photos:[{id:2,name:'selected'},{id:3,name:'keeper'}]},mediaIds:[2],
    confirm:()=>accepted,send:async()=>({ok:true,gptk:true,authed:true}),
    api:{health:async()=>({live_trash_enabled:true}),
      post:async(path,body)=>{calls.push(['review',body]);return {action_id:4,account:'account',keys:['selected-key']};},
      createOperation:async body=>{calls.push(['operation',body]);return {id:9};},
      approveAction:async id=>calls.push(['approve',id]),
      applyAction:async(id,dry)=>{calls.push(['apply',id,dry]);return {result:{operation_id:10}};}}};
  return {args,calls};
}
test('accident dry run never approves or consumes selection',async()=>{
 const {args,calls}=setup(true);assert.equal((await trashAccident(args)).id,9);
 assert.deepEqual(calls.map(x=>x[0]),['review','operation']);
 assert.equal(calls[0][1].preview,true);assert.equal(calls[1][1].dry_run,true);
});
test('cancelled accident confirmation queues nothing',async()=>{
 const {args,calls}=setup(false,false);assert.equal(await trashAccident(args),null);assert.deepEqual(calls,[]);
});
test('confirmed accident trash uses exact review then approval and explicit live apply',async()=>{
 const {args,calls}=setup(false);assert.equal((await trashAccident(args)).id,10);
 assert.deepEqual(calls,[['review',{media_ids:[2],preview:false}],['approve',4],['apply',4,false]]);
});
test('unreachable Google Photos leaves reviews untouched',async()=>{
 const {args,calls}=setup(false);args.send=async()=>({ok:false});
 await assert.rejects(trashAccident(args));assert.deepEqual(calls,[]);
});

import {trashCleanupPage} from '../lib/accident-trash.js';
test('page selection produces one snapshot and one operation after confirmation',async()=>{
 const {args,calls}=setup(false);
 const selections=[{group:{id:1,photos:[{id:2,name:'a'},{id:3,name:'b'}]},mediaIds:[2,3]},
 {group:{id:4,photos:[{id:5,name:'c'},{id:6,name:'keep'}]},mediaIds:[5]}];
 let confirmation='';
 await trashCleanupPage({...args,category:'attempts',selections,confirm:text=>{confirmation=text;return true;}});
 assert.deepEqual(calls.map(x=>x[0]),['review','approve','apply']);
 assert.deepEqual(calls[0][1].groups,[{group_id:1,media_ids:[2,3],allow_all:true},{group_id:4,media_ids:[5],allow_all:false}]);
 assert.match(confirmation,/1 entire group/);
});
test('cancelled global confirmation makes no writes',async()=>{
 const {args,calls}=setup(false,false);
 await trashCleanupPage({...args,category:'accidents',selections:[{group:args.group,mediaIds:[2]}]});
 assert.deepEqual(calls,[]);
});
