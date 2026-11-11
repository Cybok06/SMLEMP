import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from functools import wraps
from datetime import datetime
from bson import ObjectId
from flask import Flask,session
import mongomock
import pytest

@pytest.fixture
def handover_flow(monkeypatch):
    database=mongomock.MongoClient().test
    fake_db=ModuleType('db');fake_db.db=database;monkeypatch.setitem(sys.modules,'db',fake_db)
    fake_audit=ModuleType('services.activity_audit');fake_audit.audit_action=lambda *args,**kwargs:lambda fn:fn;monkeypatch.setitem(sys.modules,'services.activity_audit',fake_audit)
    fake_login=ModuleType('login')
    def identity():
        id=session.get('user_id');user=database.users.find_one({'_id':ObjectId(id)}) if id else {}
        return {'user_id':id,'role':session.get('role'),'name':(user or {}).get('name') or 'Staff'}
    fake_login.get_current_identity=identity;monkeypatch.setitem(sys.modules,'login',fake_login)
    class TestSession:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def with_transaction(self,callback,**kwargs):
            assert kwargs['read_concern'].level=='snapshot'
            return callback(None) # mongomock cannot run MongoDB transactions.
    monkeypatch.setattr(database.client,'start_session',lambda:TestSession())
    modules={};root=Path(__file__).resolve().parents[1]
    for name in ['hr_backend.hr_dashboard','hr_backend.hr_exits','hr_backend.handover_api','routes.transfer_customer']:
        spec=importlib.util.spec_from_file_location(name,root/Path(name.replace('.','/')+'.py'));module=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,module);spec.loader.exec_module(module);modules[name]=module
    app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'));app.secret_key='test'
    app.register_blueprint(modules['hr_backend.hr_dashboard'].hr_bp);app.register_blueprint(modules['hr_backend.handover_api'].handover_bp);app.register_blueprint(modules['routes.transfer_customer'].transfer_customer_bp)
    client=app.test_client();source,target,other,hr,manager=[ObjectId() for _ in range(5)]
    database.users.insert_many([{'_id':source,'role':'agent','name':'Departing Agent','manager_id':manager},{'_id':target,'role':'agent','name':'Receiving Agent','manager_id':manager},{'_id':other,'role':'agent','name':'Other Agent'},{'_id':hr,'role':'hr','name':'HR Officer'},{'_id':manager,'role':'manager','name':'Manager'}])
    customer=ObjectId();database.customers.insert_one({'_id':customer,'name':'Customer A','phone_number':'0241112222','agent_id':str(source),'purchases':[{'product':{'name':'Fridge','total':1000,'quantity':1,'status':'active'}},{'product':{'name':'TV','total':500,'quantity':1,'status':'delivered'}}]})
    database.payments.insert_many([{'customer_id':str(customer),'product_index':0,'payment_type':'PRODUCT','amount':400},{'customer_id':customer,'product_index':1,'payment_type':'PRODUCT','amount':500},{'customer_id':customer,'product_index':0,'payment_type':'WITHDRAWAL','amount':50}])
    database.complaints.insert_one({'customer_id':customer,'ticket_no':'INC-1','status':'In Progress','subject':'Missing cable'})
    exit_id=ObjectId();database.hr_exits.insert_one({'_id':exit_id,'employee':{'user_id':str(source),'name':'Departing Agent'},'status':'Handover In Progress','handover_required':True,'is_deleted':False,'created_at':datetime.utcnow(),'transfers':{'total_customers':1},'clearance':{'checks':{key:True for key in modules['hr_backend.hr_exits'].CLEARANCE_KEYS}}})
    def login(role='hr',id=hr):
        with client.session_transaction() as state:state.clear();state.update(role=role,user_id=str(id));state[role+'_id']=str(id)
    login()
    return client,database,login,source,target,other,hr,manager,customer,exit_id,modules

def capture(flow,**changes):
    client,db,login,source,target,other,hr,manager,customer,exit_id,modules=flow
    return client.post(f'/api/hr/exits/{exit_id}/handovers',json={'to_user_id':str(target),'customer_ids':[str(customer)],**changes})

def test_snapshot_debts_deliveries_issues_are_frozen_and_idempotent(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    result=capture(handover_flow);assert result.status_code==201,result.json
    snapshot=result.json['records'][0]['snapshot'];assert snapshot['customer_debt']==650 and snapshot['pending_delivery_count']==1 and snapshot['unresolved_issue_count']==1
    db.payments.insert_one({'customer_id':customer,'product_index':0,'amount':650,'payment_type':'PRODUCT'})
    db.customers.update_one({'_id':customer},{'$set':{'purchases.0.product.status':'delivered'}});db.complaints.update_many({},{'$set':{'status':'Resolved'}})
    assert capture(handover_flow).json['records'][0]['snapshot']==snapshot
    assert db.hr_customer_handovers.count_documents({})==1
    assert capture(handover_flow,to_user_id=str(other)).status_code==400

def test_only_receiver_acknowledges_then_hr_confirms_after_transfer(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    id=capture(handover_flow).json['records'][0]['id']
    assert client.post(f'/api/handovers/{id}/confirm',json={'confirm':True}).status_code==409
    for user in [source,other]:
        login('agent',user);assert client.post(f'/api/handovers/{id}/acknowledge',json={'accept':True}).status_code==409
    login('agent',target)
    assert client.post(f'/api/handovers/{id}/acknowledge',json={'accept':False}).status_code==400
    assert client.post(f'/api/handovers/{id}/acknowledge',json={'accept':True,'snapshot':{'customer_debt':0},'note':'Reviewed balances'}).status_code==200
    assert client.post(f'/api/handovers/{id}/acknowledge',json={'accept':True}).status_code==409
    assert client.post(f'/api/handovers/{id}/confirm',json={'confirm':True}).status_code==403
    login();assert client.post(f'/api/handovers/{id}/confirm',json={'confirm':True}).status_code==409
    db.customers.update_one({'_id':customer},{'$set':{'agent_id':str(target)}})
    assert client.post(f'/api/handovers/{id}/confirm',json={'confirm':True,'note':'Verified transfer'}).status_code==200
    assert db.hr_customer_handovers.find_one({})['snapshot']['customer_debt']==650
    assert client.post(f'/hr/exits/{exit_id}/close').status_code==200
    assert client.post(f'/hr/exits/{exit_id}/delete').status_code==409

def test_history_in_both_accounts_with_no_other_agent_access(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    capture(handover_flow)
    for user in [source,target]:
        login('agent',user);assert client.get('/api/agent/handovers?user_id='+str(other)).json['pagination']['total']==1
        assert client.get(f'/api/hr/exits/{exit_id}/handovers').status_code==403
    login('agent',other);assert client.get('/api/agent/handovers').json['pagination']['total']==0
    login();assert client.get(f'/api/hr/employees/{source}/handovers').json['pagination']['total']==1

def test_clearance_and_status_cannot_bypass_required_acknowledgements(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    for path,payload in [('close',{}),('status',{'status':'Closed'}),('transfer/verify',{}),('clearance',{'checks':{'customers_transferred':True}})]:
        assert client.post(f'/hr/exits/{exit_id}/{path}',json=payload).status_code==409
    capture(handover_flow)
    assert client.post(f'/hr/exits/{exit_id}/close').status_code==409

def test_manager_transfer_from_archived_source_captures_before_ownership_moves(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    user=db.users.find_one({'_id':source});db.Exited.insert_one(user);db.users.delete_one({'_id':source})
    login('manager',manager)
    payload={'from_agent_id':str(source),'to_agent_id':str(target),'customer_ids':[str(customer)]}
    assert client.post('/transfer-customers/run',json={**payload,'dry_run':True}).status_code==200
    assert db.hr_customer_handovers.count_documents({})==0
    result=client.post('/transfer-customers/run',json=payload);assert result.status_code==200,result.json
    assert result.json['updated_customers']==1
    assert db.customers.find_one({'_id':customer})['agent_id']==str(target)
    assert db.hr_customer_handovers.find_one({})['snapshot']['customer_debt']==650
    assert db.hr_customer_handovers.find_one({})['ownership_transferred_at']

def test_capture_rejects_wrong_account_receiver_and_closed_exit(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    assert capture(handover_flow,to_user_id=str(hr)).status_code==400
    assert capture(handover_flow,customer_ids=[str(ObjectId())]).status_code==400
    db.hr_exits.update_one({'_id':exit_id},{'$set':{'status':'Closed'}})
    assert capture(handover_flow).status_code==409


def test_invalid_amounts_block_capture_and_transfer_without_changing_owner(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    db.payments.insert_one({'customer_id':customer,'product_index':0,'amount':'invalid'})
    assert capture(handover_flow).status_code==400
    assert db.hr_customer_handovers.count_documents({})==0
    login('manager',manager)
    result=client.post('/transfer-customers/run',json={'from_agent_id':str(source),'to_agent_id':str(target)})
    assert result.status_code==409
    assert db.customers.find_one({'_id':customer})['agent_id']==str(source)


def test_original_account_coverage_and_other_agent_cannot_replace_snapshot(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    db.hr_exits.update_one({'_id':exit_id},{'$set':{'handover_customer_ids':[str(ObjectId())]}})
    capture(handover_flow)
    assert client.post(f'/hr/exits/{exit_id}/close').status_code==409
    db.users.update_one({'_id':other},{'$set':{'manager_id':manager}})
    login('manager',manager)
    result=client.post('/transfer-customers/run',json={'from_agent_id':str(source),'to_agent_id':str(other)})
    assert result.status_code==409
    assert db.customers.find_one({'_id':customer})['agent_id']==str(source)


def test_closed_cards_and_deposits_do_not_inflate_collectible_debt(handover_flow):
    from services.exit_handovers import account_snapshot
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    db.customers.update_one({'_id':customer},{'$push':{'purchases':{'product':{'name':'Cancelled cooker','total':900,'status':'closed'}}}})
    db.payments.insert_one({'customer_id':customer,'product_index':0,'payment_type':'REGISTRATION','amount':9999})
    snapshot=account_snapshot(db,db.customers.find_one({'_id':customer}))
    assert snapshot['customer_debt']==650 and snapshot['pending_delivery_count']==1


def test_owner_canonical_agent_takes_precedence_over_creator_fields(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    db.customers.update_one({'_id':customer},{'$set':{'agent_id':str(other),'user_id':str(source)}})
    assert client.get(f'/api/hr/exits/{exit_id}/handover-candidates').json['total']==0
    assert capture(handover_flow).status_code==400


def test_record_pagination_counts_and_agent_filter_cannot_leak(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    record=capture(handover_flow).json['records'][0]
    document=db.hr_customer_handovers.find_one({})
    for index in range(25):
        clone={**document,'_id':ObjectId(),'customer_id':str(ObjectId())};db.hr_customer_handovers.insert_one(clone)
    login('agent',target)
    data=client.get('/api/agent/handovers?page=2').json
    assert len(data['records'])==1 and data['pagination']['total']==26
    assert data['totals']['customer_debt']==16900
    login('agent',other)
    assert client.get('/api/agent/handovers?to_user_id='+str(target)).json['pagination']['total']==0


def test_locked_receivers_cannot_be_assigned(handover_flow):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    db.users.update_one({'_id':target},{'$set':{'account_locked':True}})
    assert capture(handover_flow).status_code==400
    assert str(target) not in [row['id'] for row in client.get('/api/hr/handover-receivers').json['receivers']]
