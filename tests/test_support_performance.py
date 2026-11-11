from datetime import datetime,timedelta
from bson import ObjectId
import pytest
from test_customer_support_targets import flow, target, visit_payload, activity
from test_delivery_followups import delivery_flow, review
from test_repeat_purchases import opportunity

def test_personal_counts_exclude_unlogged_incomplete_missed_and_other_officers(flow):
    client,db,_,login,officer,other,customer=flow
    target(flow,'calls','daily',3)
    now=datetime.utcnow()
    db.customer_support_calls.insert_many([
        {'officer_id':officer,'started_at':now,'type':'Outbound','enrichment_status':'complete'},
        {'officer_id':officer,'started_at':now,'type':'Missed','enrichment_status':'complete'},
        {'officer_id':officer,'started_at':now,'type':'Outbound','enrichment_status':'needs_update'},
        {'officer_id':other,'started_at':now,'type':'Outbound','enrichment_status':'complete'},
        {'started_at':now,'type':'Outbound','enrichment_status':'complete'},
        {'officer_id':officer,'started_at':now-timedelta(days=15),'type':'Outbound'}])
    db.customer_support_manual_activities.insert_many([
        {'created_by':officer,'category':'Interaction','occurred_at':now,'channel':'Call'},
        {'created_by':other,'category':'Interaction','occurred_at':now,'field_visit_id':ObjectId()},
        {'created_by':officer,'category':'Note','occurred_at':now}])
    data=client.get('/api/customer-support/targets?officer_id='+other).json
    assert data['officer_id']==officer
    assert data['targets'][0]['actual']==1
    counts=data['performance']['daily']
    assert counts['completed_calls']==1 and counts['recorded_interactions']==1 and counts['field_interactions']==0
    assert counts['assigned_followups']==0
    login('admin')
    admin=client.get('/api/admin/customer-support/progress?officer_id='+officer).json
    login('executive')
    executive=client.get('/api/executive/customer-support/progress?officer_id='+officer).json
    assert admin['performance']==executive['performance']==data['performance']

def test_issue_resolution_requires_actor_and_event_date(flow):
    client,db,_,_,officer,other,customer=flow
    now=datetime.utcnow()
    first=ObjectId()
    db.complaints.insert_many([{'_id':first,'status':'Assigned'}, {'status':'Resolved','created_by':officer,'date_closed':now},
        {'status':'Resolved','resolution_history':[{'actor_id':other,'actor_role':'customer_support','at':now}]}])
    assert client.patch('/api/customer-support/tickets/'+str(first),json={'status':'Resolved','resolution_notes':'Replaced cable'}).status_code==200
    assert client.patch('/api/customer-support/tickets/'+str(first),json={'status':'Closed'}).status_code==200
    assert len(db.complaints.find_one({'_id':first})['resolution_history'])==1
    assert client.get('/api/customer-support/targets').json['performance']['daily']['issues_resolved']==1
    earlier=(now-timedelta(days=10)).date().isoformat()
    assert client.get('/api/customer-support/targets?date='+earlier).json['performance']['daily']['issues_resolved']==0

def test_delivery_issue_resolution_counted_once_for_actual_resolver(delivery_flow):
    client,db,_,officer,_,_,id=delivery_flow
    tomorrow=(datetime.utcnow()+timedelta(days=1)).date().isoformat()
    assert client.post(f'/api/customer-support/delivery-followups/{id}',json=review(satisfaction='issue',issue='Broken cable',next_followup_date=tomorrow)).status_code==200
    assert client.get('/api/customer-support/targets').json['performance']['daily']['issues_resolved']==0
    assert client.post(f'/api/customer-support/delivery-followups/{id}',json=review(version=1)).status_code==200
    assert client.post(f'/api/customer-support/delivery-followups/{id}',json=review(version=2)).status_code==200
    assert client.get('/api/customer-support/targets').json['performance']['daily']['issues_resolved']==1

def test_opportunity_assignments_and_linked_purchase_attributed_to_originator(opportunity):
    client,db,login,agent,other,customer,id,due=opportunity
    officer=str(db.users.find_one({'role':'customer_support','name':'Officer'})['_id'])
    now=datetime.utcnow()
    db.tasks.insert_many([{'created_by':officer,'created_by_role':'customer_support','created_at':now,'assignee_id':agent},
        {'created_by':officer,'created_by_role':'customer_support','created_at':now},
        {'created_by':str(other),'created_by_role':'customer_support','created_at':now,'assignee_id':agent}])
    db.customer_support_calls.insert_one({'officer_id':officer,'created_at':now,'follow_up':True,'follow_up_agent_id':agent})
    counts=client.get('/api/customer-support/targets').json['performance']['daily']
    assert counts['repeat_opportunities']==1 and counts['assigned_followups']==3 and counts['resulting_purchases']==0
    db.customers.update_one({'_id':ObjectId(customer)},{'$push':{'purchases':{'product':{'name':'Gas cooker','status':'active'},'purchase_date':now.isoformat()}}})
    login('agent',agent)
    assert client.patch(f'/api/agent/repeat-purchases/{id}',json={'version':0,'status':'Purchased','notes':'Customer purchased cooker','purchase_index':1}).status_code==200
    login()
    assert client.get('/api/customer-support/targets').json['performance']['daily']['resulting_purchases']==1
    # Duplicate linkage cannot inflate purchase totals.
    task=db.tasks.find_one({'_id':ObjectId(id)});task['_id']=ObjectId();db.tasks.insert_one(task)
    assert client.get('/api/customer-support/targets').json['performance']['daily']['resulting_purchases']==1

def test_period_boundaries_and_field_trip_evidence(flow):
    client,db,_,_,officer,_,customer=flow
    payload=visit_payload()
    visit=client.post('/api/customer-support/field-visits',json=payload).json['visit']
    assert activity(flow,visit['id']).status_code==201
    day=payload['started_at'][:10]
    data=client.get('/api/customer-support/targets?date='+day).json['performance']['daily']
    assert data['field_trips']==1 and data['field_interactions']==1
    assert client.get('/api/customer-support/targets').json['performance']['daily']['field_trips']==0
