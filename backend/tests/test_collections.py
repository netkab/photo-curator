import json
from datetime import datetime, timedelta
import numpy as np
from PIL import Image
from app.db import session_scope
from app.config import settings
from app.models import AccidentGroup, Media, GpItem, ReviewAction, GpOperation, PreviewClassification
from app.pipeline import collections

class Handle:
    cancelled=False
    def update(self,*args): pass

def seed(category='temporary', count=1):
    with session_scope() as s:
        ids=[]
        for i in range(count):
            m=Media(abs_path='',rel_name=f'screenshot-{i}.jpg',sha256=str(i),media_type='photo',source='google-photos-thumbnail',thumb_path=f'test-{i}.jpg',taken_at=datetime(2020,1,1)+timedelta(seconds=i))
            Image.new('RGB',(64,64),'white').save(settings.thumbs_dir/m.thumb_path)
            s.add(m);s.flush();ids.append(m.id)
            s.add(GpItem(media_id=m.id,media_key=f'm{i}',dedup_key=f'k{i}',account='stable',is_owned=True))
        g=AccidentGroup(category=category,fingerprint='test',payload=json.dumps({'members':[{'media_id':x,'reasons':['test']} for x in ids],'context_ids':[],'taken_at':'2020-01-01'}))
        s.add(g);s.flush()
        return g.id,ids

def test_temporary_individual_can_be_reviewed_with_exact_keys(client):
    gid,ids=seed()
    assert not client.get('/api/accidents').json()['groups']
    assert len(client.get('/api/accidents?category=temporary').json()['groups'])==1
    r=client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids,'preview':True})
    assert r.status_code==200,r.text
    assert r.json()['keys']==['k0'] and r.json()['protected_keys']==[]
    a=client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids}).json()['action_id']
    assert client.post(f'/api/review/{a}/approve').status_code==200
    op=client.post(f'/api/review/{a}/apply').json()
    assert op['result']['dry_run'] is True
    assert client.post(f'/api/review/{a}/apply',json={'dry_run':False}).status_code==403

def test_keeperless_flag_cannot_bypass_attempt_or_accident_keepers(client):
    gid,ids=seed('attempts')
    assert client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids}).status_code==409
    with session_scope() as s:
        a=ReviewAction(kind='delete',payload=json.dumps({'cleanup_category':'temporary','accident_group_id':gid,'items':[{'media_id':ids[0]}]}))
        s.add(a);s.flush();aid=a.id
    assert client.post(f'/api/review/{aid}/approve').status_code==409

def test_temporary_shared_alias_still_blocked(client):
    gid,ids=seed()
    with session_scope() as s:
        s.add(GpItem(media_key='alias',dedup_key='k0',account='stable',is_owned=False))
    assert client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids}).status_code==409

def test_labels_keep_documents_separate():
    assert collections.classify_text('Invoice paid. Your delivery is arriving')=='documents'
    assert collections.classify_text('Your package tracking and delivery date')=='delivery'
    assert collections.classify_text('תפריט ארוחת ערב')=='shopping'
    assert collections.classify_text('', 'Screenshot_2020.jpg')=='screenshots'
    assert collections.classify_text('Happy birthday') is None

def test_repeated_attempts_anchor_account_time_and_content_bounds():
    t=datetime(2020,1,1)
    def row(i,h=0,account='a',seconds=0,key=None):
        return dict(id=i,hash=h,account=account,time=t+timedelta(seconds=seconds),key=key or str(i),color=np.ones(24)/24,aspect=1.3)
    rows=[row(1),row(2,15,seconds=20),row(3,2**40-1,seconds=30),row(4,seconds=180)]
    assert [[x['id'] for x in g] for g in collections.repeated_groups(rows)]==[[1,2]]
    assert not collections.repeated_groups([row(1,key='same'),row(2,key='same')])
    assert not collections.repeated_groups([row(1),row(2,account='b')])

def test_classification_cache_and_collection_isolation(client,monkeypatch):
    gid,ids=seed('accidents')
    class Reader:
        calls=0
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,path): Reader.calls+=1;return 'tracking delivery update'
    monkeypatch.setattr(collections,'LocalTextReader',Reader)
    assert collections.analyze(Handle(),'temporary')['groups']==1
    assert collections.analyze(Handle(),'temporary')['groups']==1
    assert Reader.calls==1
    assert len(client.get('/api/accidents').json()['groups'])==1
    g=client.get('/api/accidents?category=temporary').json()['groups'][0]
    client.post(f"/api/accidents/{g['id']}/ignore")
    assert collections.analyze(Handle(),'temporary')['groups']==0
    with session_scope() as s:
        assert s.query(ReviewAction).count()==0
        assert s.query(GpOperation).count()==0
        assert 'tracking' not in s.get(PreviewClassification,ids[0]).labels

def test_temporary_partial_selection_protects_unselected(client):
    gid,ids=seed(count=3)
    r=client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids[:2],'preview':True})
    assert r.status_code==200
    assert r.json()['protected_keys']==['k2']

def test_live_temporary_select_all_can_reach_batch_and_undo(client):
    gid,ids=seed()
    a=client.post(f'/api/accidents/{gid}/review',json={'media_ids':ids}).json()['action_id']
    assert client.post(f'/api/review/{a}/approve').status_code==200
    settings.live_trash_enabled=True
    r=client.post(f'/api/review/{a}/apply',json={'dry_run':False})
    assert r.status_code==200,r.text
    oid=r.json()['result']['operation_id']
    r=client.get(f'/api/gp/operations/{oid}/next?account=stable')
    assert r.status_code==200,r.text
    assert r.json()['batch']==['k0']
    assert client.post(f'/api/gp/operations/{oid}/result',json={'cursor':0,'succeeded':['k0']}).status_code==200
    inverse=client.post(f'/api/gp/operations/{oid}/undo')
    assert inverse.status_code==200,inverse.text
    rid=inverse.json()['id']
    batch=client.get(f'/api/gp/operations/{rid}/next?account=stable')
    assert batch.status_code==200 and batch.json()['batch']==['k0']

def test_bulk_full_attempt_group_reaches_live_batch(client):
    gid,ids=seed('attempts',3)
    body={'category':'attempts','preview':False,'groups':[{'group_id':gid,'media_ids':ids,'allow_all':True}]}
    r=client.post('/api/accidents/review-bulk',json=body)
    assert r.status_code==200,r.text
    aid=r.json()['action_id']
    assert client.post(f'/api/review/{aid}/approve').status_code==200
    settings.live_trash_enabled=True
    r=client.post(f'/api/review/{aid}/apply',json={'dry_run':False})
    assert r.status_code==200,r.text
    oid=r.json()['result']['operation_id']
    assert set(client.get(f'/api/gp/operations/{oid}/next?account=stable').json()['batch'])=={'k0','k1','k2'}

def test_bulk_full_group_requires_explicit_flag(client):
    gid,ids=seed('accidents',2)
    r=client.post('/api/accidents/review-bulk',json={'category':'accidents','preview':False,'groups':[{'group_id':gid,'media_ids':ids}]})
    assert r.status_code==409
    with session_scope() as s: assert s.query(ReviewAction).count()==0

def test_bulk_preview_preserves_groups_and_partial_selection_protects(client):
    gid,ids=seed('attempts',3)
    body={'category':'attempts','groups':[{'group_id':gid,'media_ids':ids[:2]}]}
    r=client.post('/api/accidents/review-bulk',json=body)
    assert r.status_code==200,r.text
    assert r.json()['protected_keys']==['k2']
    with session_scope() as s:
        assert s.get(AccidentGroup,gid).status=='pending'
        assert s.query(ReviewAction).count()==0
    body['groups'].append({'group_id':99999,'media_ids':[99999]})
    assert client.post('/api/accidents/review-bulk',json={**body,'preview':False}).status_code==409
    with session_scope() as s:
        assert s.get(AccidentGroup,gid).status=='pending'
        assert s.query(ReviewAction).count()==0

def test_two_groups_create_one_atomic_review(client):
    gid,ids=seed('attempts',3)
    with session_scope() as s:
        first=s.get(AccidentGroup,gid)
        p=json.loads(first.payload);p['members']=p['members'][:2];first.payload=json.dumps(p)
        second=AccidentGroup(category='attempts',fingerprint='second',payload=json.dumps({'members':[{'media_id':ids[2],'reasons':[]}],'context_ids':[],'taken_at':None}))
        s.add(second);s.flush();gid2=second.id
    r=client.post('/api/accidents/review-bulk',json={'category':'attempts','preview':False,'groups':[
        {'group_id':gid,'media_ids':ids[:2],'allow_all':True},
        {'group_id':gid2,'media_ids':ids[2:],'allow_all':True}]})
    assert r.status_code==200,r.text
    with session_scope() as s:
        assert s.query(ReviewAction).count()==1
        assert s.get(AccidentGroup,gid).status==s.get(AccidentGroup,gid2).status=='reviewed'

def test_explicit_selection_overrides_nearby_context_in_included_group(client):
    gid,ids=seed('accidents',2)
    with session_scope() as s:
        first=s.get(AccidentGroup,gid)
        p=json.loads(first.payload);p['members']=p['members'][:1];p['context_ids']=[ids[1]];first.payload=json.dumps(p)
        second=AccidentGroup(category='accidents',fingerprint='context-group',payload=json.dumps({'members':[{'media_id':ids[1],'reasons':[]}],'context_ids':[ids[0]],'taken_at':None}))
        s.add(second);s.flush();gid2=second.id
    r=client.post('/api/accidents/review-bulk',json={'category':'accidents','groups':[
        {'group_id':gid,'media_ids':ids[:1],'allow_all':True},{'group_id':gid2,'media_ids':ids[1:],'allow_all':True}]})
    assert r.status_code==200,r.text
    assert r.json()['protected_keys']==[]
