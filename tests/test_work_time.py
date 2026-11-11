import importlib.util
import sys
import types
from functools import wraps
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, date, timedelta
from unittest.mock import patch
import mongomock
import pytest
from bson import ObjectId
from flask import Flask, Blueprint, session, jsonify
from services.work_time import ROLES, summary

@pytest.fixture
def work_flow(monkeypatch):
    database=mongomock.MongoClient().db
    fake_db=types.ModuleType('db');fake_db.db=database
    login=types.ModuleType('login')
    login.get_current_identity=lambda:{'is_authenticated':bool(session.get('user_id')),'user_id':session.get('user_id'),'role':session.get('role'),'name':'Signed-in staff'}
    def role_required(*roles):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args,**kwargs):
                identity=login.get_current_identity()
                if not identity['is_authenticated']:return jsonify(ok=False,message='Unauthorized'),401
                if identity['role'] not in roles:return jsonify(ok=False,message='Forbidden'),403
                return fn(*args,**kwargs)
            return wrapped
        return decorator
    login.role_required=role_required
    audit=types.ModuleType('services.activity_audit');audit.audit_action=lambda *args,**kwargs:lambda fn:fn
    hr=types.ModuleType('hr_backend.hr_dashboard');hr.hr_bp=Blueprint('hr',__name__,url_prefix='/hr');hr._hr_access_guard=lambda:session.get('role') in ('hr','admin','executive')
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
    with patch.dict(sys.modules,{'db':fake_db,'login':login,'services.activity_audit':audit,'hr_backend.hr_dashboard':hr}):
        leave=load('hr_backend.hr_attendance','hr_backend/hr_attendance.py')
        attendance=load('_work_time_attendance','routes/attendance.py')
        root=Path(__file__).resolve().parents[1]
        app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'));app.secret_key='work-test';app.testing=True;app.register_blueprint(attendance.attendance_bp);app.register_blueprint(hr.hr_bp)
        app.context_processor(lambda:{'identity':login.get_current_identity()});app.url_build_error_handlers.append(lambda error,endpoint,values:'/_unused/'+endpoint)
        client=app.test_client();people={role:ObjectId() for role in ROLES}
        database.users.insert_many([{'_id':id,'name':role.title()+' Employee','role':role,'branch':'Accra'} for role,id in people.items()])
        def signin(role='agent'):
            with client.session_transaction() as state:state.clear();state.update(role=role,user_id=str(people[role]));state[role+'_id']=str(people[role])
        now=datetime(2026,10,7,8,6,0,tzinfo=timezone.utc)
        monkeypatch.setattr(attendance,'_utc_now',lambda:now)
        signin()
        yield client,database,signin,people,attendance,leave

def config(**changes):return {'start_time':'08:00','end_time':'17:00','grace_minutes':5,'workdays':[0,1,2,3,4],**changes}
def leave_payload(**changes):return {'start_date':date.today().isoformat(),'days':1,'leave_type':'Permission','reason':'Family appointment','request_id':str(uuid4()),**changes}

@pytest.mark.parametrize('role',ROLES)
def test_all_roles_can_clock_in_out_and_request_pending_leave(work_flow,role):
    client,db,signin,people,*_=work_flow;signin(role)
    result=client.post('/attendance/api/action',json={'action':'report_to_work','user_id':'forged','at':'2001-01-01','location_status':'denied'})
    assert result.status_code==200,result.json
    doc=db.attendance_workdays.find_one({});assert doc['user_id']==str(people[role]);assert doc['report_to_work_at']=='2026-10-07T08:06:00Z';assert doc['events'][0]['location']['status']=='denied'
    assert client.post('/attendance/api/action',json={'action':'leave_office','location_status':'unavailable'}).status_code==200
    result=client.post('/attendance/api/leave',json=leave_payload(status='Approved',requested_by='Forged'));assert result.status_code==200,result.json
    leave=db.users.find_one({'_id':people[role]})['leaves'][0];assert leave['status']=='Pending' and leave['requested_by']=='Signed-in staff'
    assert client.get('/attendance/api/summary').json['summary']['pending_leave_requests']==1
    assert client.get('/log-time').status_code==200

def test_schedule_late_and_original_assessment_survives_configuration(work_flow):
    client,db,signin,people,attendance,_=work_flow;signin('executive');assert client.post('/attendance/api/settings',json=config()).status_code==200
    signin();result=client.post('/attendance/api/action',json={'action':'report_to_work','location_status':'granted','location':{'latitude':5.6037,'longitude':-0.187,'accuracy':12}})
    assert result.json['attendance']['schedule']['is_late'] and result.json['attendance']['schedule']['late_minutes']==6
    signin('admin');client.post('/attendance/api/settings',json=config(start_time='09:00'))
    signin();summary=client.get('/attendance/api/summary').json['summary'];assert summary['late_days']==1
    assert db.attendance_workdays.find_one({})['schedule']['start_time']=='08:00'
    signin('hr');analytics=client.get('/attendance/api/analytics').json;assert analytics['totals']['late_days']==1
    assert analytics['records'][0]['events'][0]['location']['latitude']==5.6037

@pytest.mark.parametrize('role',['agent','manager','inventory','hr','customer_support','accounting'])
def test_only_owners_configure_and_staff_cannot_view_team(work_flow,role):
    client,db,signin,*_=work_flow;signin(role)
    assert client.post('/attendance/api/settings',json=config()).status_code==403
    if role not in {'hr','manager'}:assert client.get('/attendance/api/analytics').status_code==403
    else:assert client.get('/attendance/api/analytics').status_code==200

@pytest.mark.parametrize('changes',[{'start_time':'bad'},{'end_time':'07:00'},{'grace_minutes':-1},{'grace_minutes':True},{'grace_minutes':121},{'workdays':[]},{'workdays':[7]},{'workdays':[False]}])
def test_schedule_validation(work_flow,changes):
    client,db,signin,*_=work_flow;signin('admin');assert client.post('/attendance/api/settings',json=config(**changes)).status_code==400
    assert db.work_time_settings.count_documents({})==0

@pytest.mark.parametrize('payload',[{'location_status':'granted'},{'location_status':'bogus'},{'location':{'latitude':91,'longitude':1,'accuracy':1}},{'location':{'latitude':1,'longitude':1,'accuracy':-1}},{'location':{'latitude':'NaN','longitude':1,'accuracy':1}}])
def test_invalid_geolocation_is_rejected(work_flow,payload):
    client,db,*_=work_flow;assert client.post('/attendance/api/action',json={'action':'report_to_work',**payload}).status_code==400
    assert db.attendance_workdays.count_documents({})==0

def test_transitions_and_concurrent_write_preserve_events(work_flow,monkeypatch):
    client,db,signin,people,attendance,_=work_flow
    assert client.post('/attendance/api/action',json={'action':'leave_office'}).status_code==409
    assert client.post('/attendance/api/action',json={'action':'report_to_work'}).status_code==200
    assert client.post('/attendance/api/action',json={'action':'report_to_work'}).status_code==409
    original=db.attendance_workdays.update_one
    def race(query,update,*args,**kwargs):
        original({'user_id':str(people['agent'])},{'$inc':{'version':1}});return original(query,update,*args,**kwargs)
    monkeypatch.setattr(db.attendance_workdays,'update_one',race)
    assert client.post('/attendance/api/action',json={'action':'leave_office'}).status_code==409
    assert len(db.attendance_workdays.find_one({})['events'])==1

def test_self_service_scope_overlap_retry_and_hr_review(work_flow):
    client,db,signin,people,*_=work_flow
    assert client.post('/attendance/api/leave',json=leave_payload(employee_id=str(people['manager']))).status_code==403
    payload=leave_payload();first=client.post('/attendance/api/leave',json=payload);id=first.json['leave_id']
    assert client.post('/attendance/api/leave',json=payload).json['leave_id']==id
    assert client.post('/attendance/api/leave',json=leave_payload()).status_code==400
    url='/hr/leave/request/'+str(people['agent'])+'/'+id
    assert client.post(url+'/approve',json={}).status_code==403
    signin('hr');assert client.post(url+'/approve',json={}).status_code==200
    assert db.users.find_one({'_id':people['agent']})['leaves'][0]['approved_by_id']==str(people['hr'])
    assert client.post(url+'/reject',json={'rejected_reason':'Changed mind'}).status_code==409
    signin();assert client.get('/attendance/api/summary').json['leaves'][0]['status']=='Approved'
    signin('hr');id=client.post('/attendance/api/leave',json=leave_payload()).json['leave_id'];url='/hr/leave/request/'+str(people['hr'])+'/'+id
    assert client.post(url+'/approve',json={}).status_code==403
    signin('executive');assert client.post(url+'/approve',json={}).status_code==200

def test_leave_rejection_and_entitlement(work_flow):
    client,db,signin,people,*_=work_flow
    id=client.post('/attendance/api/leave',json=leave_payload()).json['leave_id'];signin('hr');url='/hr/leave/request/'+str(people['agent'])+'/'+id
    assert client.post(url+'/reject',json={}).status_code==400
    assert client.post(url+'/reject',json={'rejected_reason':'Not available on that date'}).status_code==200
    signin();assert client.get('/attendance/api/summary').json['leaves'][0]['rejected_reason']=='Not available on that date'
    db.users.update_one({'_id':people['agent']},{'$set':{'leave_policy':{'types':{'Annual':{'max_days':0}}}}})
    id=client.post('/attendance/api/leave',json=leave_payload(leave_type='Annual')).json['leave_id'];signin('hr')
    assert client.post('/hr/leave/request/'+str(people['agent'])+'/'+id+'/approve',json={}).status_code==400

def test_analytics_separates_leave_missing_weekends_and_future(work_flow, monkeypatch):
    # This scenario is explicitly about future days relative to October 7.
    # Pin its clock so running the suite later does not change the expectation.
    class ScenarioDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 7, 12, tzinfo=timezone.utc).astimezone(tz) if tz else cls(2026, 10, 7, 12)
    monkeypatch.setattr('services.work_time.datetime', ScenarioDateTime)
    client,db,signin,people,*_=work_flow
    policy={**config(grace_minutes=0),'tracking_from':'2026-10-01','timezone':'Africa/Accra','effective_date':'2026-10-01','created_at':'2026-10-01T00:00:00Z'}
    db.work_time_settings.insert_one({'_id':'organization',**policy,'history':[policy]})
    db.users.update_one({'_id':people['agent']},{'$set':{'leaves':[{'_id':ObjectId(),'start_date':'2026-10-02','days':3,'leave_type':'Permission','status':'Approved'},{'_id':ObjectId(),'start_date':'2026-10-06','days':1,'status':'Pending'},{'_id':ObjectId(),'start_date':'2026-10-08','days':3,'status':'Approved'}]}})
    db.attendance_workdays.insert_one({'user_id':str(people['agent']),'attendance_date':'2026-10-01','report_to_work_at':'2026-10-01T08:00:00Z'})
    user=db.users.find_one({'_id':people['agent']});report=summary(db,user,date(2026,10,1),date(2026,10,7))
    assert report['approved_absence_dates']==['2026-10-02']
    assert report['approved_absence_occasions']==1
    assert report['missing_dates']==['2026-10-05','2026-10-06']
    assert report['pending_leave_requests']==1

def test_privacy_and_invalid_report_dates(work_flow):
    client,db,signin,people,*_=work_flow;client.post('/attendance/api/action',json={'action':'report_to_work'})
    signin('manager');assert client.get('/attendance/api/history?user_id='+str(people['agent'])).json['history']==[]
    assert client.get('/attendance/api/summary?from=bad').status_code==400
    assert client.get('/attendance/api/summary?from=2020-01-01&to=2026-10-07').status_code==400
    with client.session_transaction() as state:state.clear()
    assert client.get('/attendance/api/today').status_code==401
