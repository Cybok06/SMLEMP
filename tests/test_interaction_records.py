from datetime import datetime, timedelta
import pytest
from bson import ObjectId
from test_customer_support_targets import flow, visit_payload

def record(**changes):
    return {'category':'Interaction','interaction_record':True,'title':'Delivery conversation','description':'Customer confirmed receipt','channel':'Call','branch':'Accra','salesperson':'Sales Agent','delivery_status':'Delivered','feedback':'Happy with fridge','issues':'None','requested_products':'Gas cooker','action_taken':'Assigned sales follow-up','next_followup':'Call to discuss pricing','next_followup_at':(datetime.utcnow()+timedelta(days=2)).isoformat(),**changes}

def test_call_activity_records_details_and_authenticated_performer(flow):
    client,db,_,_,officer,_,customer=flow
    result=client.post(f'/api/customer-support/customers/{customer}/activities',json=record(created_by='spoof',created_by_name='Spoof'))
    assert result.status_code==201,result.json
    row=db.customer_support_manual_activities.find_one({'_id':ObjectId(result.json['id'])})
    assert row['created_by']==officer and row['created_by_name']=='Officer'
    assert row['channel']=='Call' and 'field_visit_id' not in row
    activity=client.get(f'/api/customer-support/customers/{customer}/activity').json['activities'][0]
    assert activity['interaction']['requested_products']=='Gas cooker'
    assert activity['performedBy']['id']==officer
    for value in ['Happy with fridge','Sales Agent','Assigned sales follow-up','Performed by Officer']:
        assert value in activity['detail']

@pytest.mark.parametrize('changes',[{'next_followup':''},{'branch':''},{'feedback':''},{'action_taken':''},{'delivery_status':'bad'},{'channel':'bad'},{'feedback':'x'*2001},{'next_followup_at':'bad'},{'next_followup_at':'2000-01-01T00:00'}])
def test_structured_record_validation(flow,changes):
    client,db,_,_,_,_,customer=flow
    assert client.post(f'/api/customer-support/customers/{customer}/activities',json=record(**changes)).status_code==400
    assert db.customer_support_manual_activities.count_documents({})==0

def test_field_records_keep_visit_link_and_target_count(flow):
    client,db,_,_,officer,_,customer=flow
    visit=client.post('/api/customer-support/field-visits',json=visit_payload())
    assert visit.status_code==201,visit.json
    id=visit.json['visit']['id']
    result=client.post(f'/api/customer-support/customers/{customer}/activities',json=record(channel='Field visit',field_visit_id=id,occurred_at=visit_payload()['started_at']))
    assert result.status_code==201,result.json
    assert db.customer_support_manual_activities.find_one({})['field_visit_id']==ObjectId(id)

def test_call_form_details_are_retained_in_activity_and_on_edit(flow):
    client,db,_,_,officer,_,customer=flow
    response=client.post('/api/customer-support/calls',json={**record(), 'customer_id':customer,'type':'Outbound','purpose':'Delivery confirmation','outcome':'Resolved','department':'Support'})
    assert response.status_code==201,response.json
    row=response.json['call']
    assert row['interaction']['feedback']=='Happy with fridge'
    assert db.customer_support_manual_activities.count_documents({})==0 # One call, one record.
    activity=client.get(f'/api/customer-support/customers/{customer}/activity').json['activities'][0]
    assert 'Requested products: Gas cooker' in activity['detail']
    result=client.patch('/api/customer-support/calls/'+row['_id'],json={**record(feedback='Updated feedback'),'purpose':'Follow-up','outcome':'Resolved','department':'Support'})
    assert result.status_code==200 and result.json['call']['interaction']['feedback']=='Updated feedback'
    assert db.customer_support_calls.find_one({})['officer_id']==officer
