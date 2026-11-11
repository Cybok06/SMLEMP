from datetime import datetime
from flask import Blueprint, jsonify, request, session, current_app
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError
from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import get_current_identity
from services.exit_handovers import oid, capture_handover, account_snapshot, ensure_indexes, ownership_query

handover_bp = Blueprint('exit_handovers',__name__)

@handover_bp.record_once
def setup(_state): ensure_indexes(db)

def identity():
    actor = get_current_identity() or {}
    return {'id':str(actor.get('user_id') or session.get('hr_id') or session.get('agent_id') or ''), 'name':actor.get('name') or 'Staff', 'role':actor.get('role') or session.get('role') or ''}

def hr_access():
    from hr_backend.hr_dashboard import _hr_access_guard
    return bool(_hr_access_guard() and identity()['id'])

def encode(value):
    if isinstance(value,datetime): return value.isoformat()+'Z'
    if isinstance(value,list): return [encode(item) for item in value]
    if isinstance(value,dict): return {('id' if key=='_id' else key):encode(item) for key,item in value.items()}
    return str(value) if value.__class__.__name__ == 'ObjectId' else value

def exit_doc(exit_id): return db.hr_exits.find_one({'_id':oid(exit_id),'is_deleted':{'$ne':True}})

def owned_query(user_id): return ownership_query(user_id)


def receiver(user_id):
    row=db.users.find_one({'_id':oid(user_id),'role':'agent','is_deleted':{'$ne':True},'is_active':{'$ne':False},'employment_status':{'$ne':'Exited'},'account_locked':{'$ne':True},'status':{'$nin':['Not Active','not active','Disabled','disabled','Inactive','inactive','Exited']}})
    return row

@handover_bp.get('/api/hr/handover-receivers')
def receivers():
    if not hr_access(): return jsonify(ok=False,message='Forbidden'),403
    rows=db.users.find({'role':'agent','is_deleted':{'$ne':True},'is_active':{'$ne':False},'employment_status':{'$ne':'Exited'},'account_locked':{'$ne':True},'status':{'$nin':['Not Active','not active','Disabled','disabled','Inactive','inactive','Exited']}}).sort('name',1)
    return jsonify(ok=True,receivers=[{'id':str(row['_id']),'name':row.get('name') or row.get('username') or 'Salesperson','branch':row.get('branch') or ''} for row in rows])

@handover_bp.get('/api/hr/exits/<exit_id>/handover-candidates')
def candidates(exit_id):
    if not hr_access(): return jsonify(ok=False,message='Forbidden'),403
    doc=exit_doc(exit_id)
    if not doc: return jsonify(ok=False,message='Exit not found.'),404
    try: page=max(1,int(request.args.get('page',1)))
    except ValueError: return jsonify(ok=False,message='Invalid page.'),400
    query=owned_query(doc['employee']['user_id']); total=db.customers.count_documents(query)
    rows=[]
    try:
        for customer in db.customers.find(query).sort('_id',1).skip((page-1)*25).limit(25):
            snapshot=account_snapshot(db,customer)
            snapshot['already_captured']=bool(db.hr_customer_handovers.find_one({'exit_id':exit_id,'customer_id':str(customer['_id'])}))
            rows.append(snapshot)
    except ValueError as error: return jsonify(ok=False,message=str(error)),400
    return jsonify(ok=True,accounts=rows,total=total,page=page,total_pages=max(1,(total+24)//25))

@handover_bp.post('/api/hr/exits/<exit_id>/handovers')
def capture(exit_id):
    if not hr_access(): return jsonify(ok=False,message='Forbidden'),403
    doc=exit_doc(exit_id)
    if not doc: return jsonify(ok=False,message='Exit not found.'),404
    if doc.get('status')=='Closed': return jsonify(ok=False,message='This exit is closed.'),409
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict): return jsonify(ok=False,message='Invalid handover.'),400
    target=receiver(data.get('to_user_id')); ids=data.get('customer_ids')
    if not target or str(target['_id'])==doc['employee']['user_id']: return jsonify(ok=False,message='Select a different active receiving salesperson.'),400
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not oid(value) for value in ids): return jsonify(ok=False,message='Select 1 to 100 customer accounts.'),400
    ids=list(dict.fromkeys(str(value) for value in ids)); note=str(data.get('note') or '').strip()
    if len(note)>2000: return jsonify(ok=False,message='Notes must be 2,000 characters or fewer.'),400
    query={'$and':[owned_query(doc['employee']['user_id']),{'_id':{'$in':[oid(value) for value in ids]}}]}
    def work(session):
        options = {"session":session} if session is not None else {}
        customers=list(db.customers.find(query, **options))
        if len(customers)!=len(ids): raise ValueError('Each selected account must still belong to the exiting salesperson. Capture before transferring.')
        rows=[capture_handover(db,doc,customer,target,identity(),note,session=session) for customer in customers]
        db.hr_exits.update_one({'_id':doc['_id']},{'$set':{'handover_required':True}}, **options)
        return rows
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    try:
        with db.client.start_session() as session:
            rows=session.with_transaction(work,read_concern=ReadConcern('snapshot'),write_concern=WriteConcern('majority'))
    except ValueError as error: return jsonify(ok=False,message=str(error)),400
    except PyMongoError:
        current_app.logger.exception('Unable to confirm permanent handover capture')
        return jsonify(ok=False,message='Unable to confirm handover capture. Refresh the handover history before retrying.'),503
    return jsonify(ok=True,records=[encode(row) for row in rows],message='Permanent handover snapshots captured.'),201

def list_records(query):
    try: page=max(1,int(request.args.get('page',1)))
    except ValueError: return jsonify(ok=False,message='Invalid page.'),400
    count=db.hr_customer_handovers.count_documents(query); pages=max(1,(count+24)//25);page=min(page,pages)
    totals={'customer_debt':0,'pending_deliveries':0,'unresolved_issues':0,'awaiting_acknowledgement':0,'awaiting_hr_confirmation':0}
    for row in db.hr_customer_handovers.find(query,{'snapshot.customer_debt':1,'snapshot.pending_delivery_count':1,'snapshot.unresolved_issue_count':1,'acknowledgement':1,'hr_confirmation':1}):
        snap=row['snapshot'];totals['customer_debt']+=snap['customer_debt'];totals['pending_deliveries']+=snap['pending_delivery_count'];totals['unresolved_issues']+=snap['unresolved_issue_count']
        if not row.get('acknowledgement'):totals['awaiting_acknowledgement']+=1
        elif not row.get('hr_confirmation'):totals['awaiting_hr_confirmation']+=1
    totals['customer_debt']=round(totals['customer_debt'],2)
    rows=list(db.hr_customer_handovers.find(query).sort('captured_at',-1).skip((page-1)*25).limit(25))
    return jsonify(ok=True,records=encode(rows),totals=totals,pagination={'page':page,'total':count,'total_pages':pages},viewer=identity())

@handover_bp.get('/api/hr/exits/<exit_id>/handovers')
def hr_exit_history(exit_id):
    if not hr_access(): return jsonify(ok=False,message='Forbidden'),403
    if not db.hr_exits.find_one({'_id':oid(exit_id)}): return jsonify(ok=False,message='Exit not found.'),404
    return list_records({'exit_id':exit_id})

@handover_bp.get('/api/hr/employees/<user_id>/handovers')
def hr_employee_history(user_id):
    if not hr_access(): return jsonify(ok=False,message='Forbidden'),403
    return list_records({'$or':[{'from_user_id':user_id},{'to_user_id':user_id}]})

@handover_bp.get('/api/agent/handovers')
def agent_history():
    actor=identity()
    if actor['role']!='agent' or not actor['id']:return jsonify(ok=False,message='Forbidden'),403
    return list_records({'$or':[{'from_user_id':actor['id']},{'to_user_id':actor['id']}]})

@handover_bp.post('/api/handovers/<record_id>/acknowledge')
def acknowledge(record_id):
    actor=identity()
    if actor['role']!='agent' or not actor['id']:return jsonify(ok=False,message='Only the receiving salesperson may acknowledge.'),403
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict) or data.get('accept') is not True:return jsonify(ok=False,message='Confirm that you reviewed and accept the recorded handover.'),400
    note=str(data.get('note') or '').strip()
    if len(note)>2000:return jsonify(ok=False,message='Notes are too long.'),400
    row=db.hr_customer_handovers.find_one_and_update({'_id':oid(record_id),'to_user_id':actor['id'],'acknowledgement':None},
        {'$set':{'acknowledgement':{'by':actor,'at':datetime.utcnow(),'note':note}},'$inc':{'version':1}},return_document=ReturnDocument.AFTER)
    if not row:return jsonify(ok=False,message='Handover not assigned to you or already acknowledged.'),409
    return jsonify(ok=True,record=encode(row))

@handover_bp.post('/api/handovers/<record_id>/confirm')
def confirm(record_id):
    actor=identity()
    if not hr_access() or actor['role']!='hr' or not actor['id']:return jsonify(ok=False,message='An HR officer must confirm the handover.'),403
    row=db.hr_customer_handovers.find_one({'_id':oid(record_id)})
    if not row:return jsonify(ok=False,message='Handover not found.'),404
    if not row.get('acknowledgement'):return jsonify(ok=False,message='The receiving salesperson must acknowledge first.'),409
    # Confirm the account reached the agreed recipient, not just that it left the source.
    customer=db.customers.find_one({'_id':oid(row['customer_id']),'agent_id':{'$in':[row['to_user_id'],oid(row['to_user_id'])]}})
    if not customer:return jsonify(ok=False,message='Manager must transfer this account to the recorded receiving salesperson before HR confirmation.'),409
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict) or data.get('confirm') is not True:return jsonify(ok=False,message='Confirm that you verified this handover.'),400
    note=str(data.get('note') or '').strip()
    if len(note)>2000:return jsonify(ok=False,message='Notes are too long.'),400
    updated=db.hr_customer_handovers.find_one_and_update({'_id':row['_id'],'hr_confirmation':None,'acknowledgement':{'$ne':None}},
        {'$set':{'hr_confirmation':{'by':actor,'at':datetime.utcnow(),'note':note}},'$inc':{'version':1}},return_document=ReturnDocument.AFTER)
    if not updated:return jsonify(ok=False,message='Handover already confirmed.'),409
    return jsonify(ok=True,record=encode(updated))
