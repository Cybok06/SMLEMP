from datetime import datetime, timedelta
import pytest
from bson import ObjectId
from test_customer_support_targets import flow
from test_delivery_followups import delivery_flow, review

@pytest.fixture
def opportunity(delivery_flow):
    client, database, login, officer, customer, package, task_id = delivery_flow
    agent, other = ObjectId(), ObjectId()
    database.users.insert_many([{'_id':agent,'role':'agent','name':'Sales Agent'},{'_id':other,'role':'agent','name':'Other Agent'}])
    due=(datetime.utcnow()+timedelta(days=2)).date().isoformat()
    payload=review(repeat_interest='yes',requested_product='Gas cooker',repeat_agent_id=str(agent),repeat_followup_date=due,repeat_notes='Customer wants a cooker')
    response=client.post(f'/api/customer-support/delivery-followups/{task_id}',json=payload)
    assert response.status_code==200,response.json
    return client,database,login,agent,other,customer,task_id,due

def test_agent_scope_search_and_role_boundaries(opportunity):
    client,db,login,agent,other,customer,id,due=opportunity
    login('agent',other)
    assert client.get('/api/agent/repeat-purchases?q=Gas').json['pagination']['total']==0
    assert client.get(f'/api/agent/repeat-purchases/{id}').status_code==404
    assert client.patch(f'/api/agent/repeat-purchases/{id}',json={}).status_code==404
    login('agent',agent)
    assert client.get('/api/agent/repeat-purchases?q=Gas').json['pagination']['total']==1
    assert client.get('/api/customer-support/repeat-purchases').status_code==403
    login()
    assert client.get('/api/agent/repeat-purchases').status_code==403

def test_progress_purchase_and_terminal_guards(opportunity):
    client,db,login,agent,other,customer,id,due=opportunity
    login('agent',agent)
    path=f'/api/agent/repeat-purchases/{id}'
    body={'version':0,'status':'In Progress','notes':'Discussed pricing','next_followup_date':due}
    assert client.patch(path,json=body).status_code==200
    assert client.patch(path,json=body).status_code==409
    body.update(version=1,status='Purchased',purchase_index=0)
    assert client.patch(path,json=body).status_code==400
    db.customers.update_one({'_id':ObjectId(customer)},{'$push':{'purchases':{'product':{'name':'Gas cooker','status':'active'},'purchase_date':datetime.utcnow().isoformat()}}})
    body['purchase_index']=1
    result=client.patch(path,json=body)
    assert result.status_code==200,result.json
    assert result.json['opportunity']['purchase']['product_name']=='Gas cooker'
    assert result.json['opportunity']['next_followup_date']==''
    assert client.patch(path,json={**body,'version':2}).status_code==409
    login()
    assert client.get('/api/customer-support/repeat-purchases?status=Purchased').json['pagination']['total']==1
    # A later delivery review cannot reset the agent's work.
    assert client.post(f'/api/customer-support/delivery-followups/{id}',json=review(version=1)).status_code==200
    assert db.tasks.find_one({'_id':ObjectId(id)})['repeat_purchase']['status']=='Purchased'

def test_reassignment_closure_and_history(opportunity):
    client,db,login,agent,other,customer,id,due=opportunity
    assert client.patch(f'/api/customer-support/repeat-purchases/{id}/assignment',json={'version':0,'agent_id':'bad','next_followup_date':due,'notes':'Retry'}).status_code==400
    response=client.patch(f'/api/customer-support/repeat-purchases/{id}/assignment',json={'version':0,'agent_id':str(other),'next_followup_date':due,'notes':'New territory agent'})
    assert response.status_code==200,response.json
    login('agent',agent)
    assert client.get(f'/api/agent/repeat-purchases/{id}').status_code==404
    login('agent',other)
    path=f'/api/agent/repeat-purchases/{id}'
    body={'version':1,'status':'Closed','notes':'Customer declined'}
    assert client.patch(path,json=body).status_code==400
    result=client.patch(path,json={**body,'closure_reason':'Budget unavailable'})
    assert result.status_code==200
    assert len(result.json['opportunity']['history'])==3
    assert result.json['opportunity']['closure_reason']=='Budget unavailable'
    login()
    assert client.patch(f'/api/customer-support/repeat-purchases/{id}/assignment',json={}).status_code==409

@pytest.mark.parametrize('changes',[{'repeat_interest':''},{'repeat_interest':[]},{'repeat_interest':'yes'},{'repeat_interest':'yes','requested_product':'TV','repeat_agent_id':'bad'}])
def test_interest_validation_atomic(delivery_flow,changes):
    client,db,_,_,_,_,id=delivery_flow
    assert client.post(f'/api/customer-support/delivery-followups/{id}',json=review(**changes)).status_code==400
    task=db.tasks.find_one({'_id':ObjectId(id)})
    assert task['review_version']==0 and 'repeat_purchase' not in task

def test_no_interest_or_unresolved_delivery_creates_no_opportunity(delivery_flow):
    client,db,_,_,_,_,id=delivery_flow
    assert client.post(f'/api/customer-support/delivery-followups/{id}',json=review()).status_code==200
    assert 'repeat_purchase' not in db.tasks.find_one({'_id':ObjectId(id)})
