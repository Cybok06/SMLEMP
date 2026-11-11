import importlib.util
import sys
import types
import io
from pathlib import Path
from uuid import uuid4
from datetime import date, timedelta
from unittest.mock import patch
import mongomock
import pytest
from bson import ObjectId
from flask import Flask, Blueprint, session
from services.work_time import ROLES

@pytest.fixture
def recruit_flow(monkeypatch):
    database=mongomock.MongoClient().db
    fake_db=types.ModuleType('db');fake_db.db=database
    login=types.ModuleType('login');login.get_current_identity=lambda:{'is_authenticated':bool(session.get('user_id')),'user_id':session.get('user_id'),'role':session.get('role'),'name':session.get('name','Signed-in staff')}
    hr=types.ModuleType('hr_backend.hr_dashboard');hr.hr_bp=Blueprint('hr',__name__,url_prefix='/hr');hr._hr_access_guard=lambda:session.get('role') in ('hr','admin','executive')
    audit=types.ModuleType('services.activity_audit');audit.audit_action=lambda *args,**kwargs:lambda fn:fn
    directory=types.ModuleType('hr_backend.hr_employees_directory');directory.RECRUITMENT_DEFAULT_STEPS=[];directory.users_col=database.users
    roles=types.ModuleType('hr_backend.hr_roles');roles.roles_col=database.hr_roles;roles.DEFAULT_ROLE_KEYS=set(ROLES);roles.normalize_role_key=lambda value:value.lower().replace(' ','_')
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
    class FakeSession:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def with_transaction(self,fn,**kwargs):
            assert kwargs['read_concern'].document=={'level':'snapshot'}
            backup={name:list(database[name].find({})) for name in database.list_collection_names()}
            try:return fn(None)
            except Exception:
                for name in database.list_collection_names():
                    database[name].delete_many({})
                    if backup.get(name):database[name].insert_many(backup[name])
                raise
    monkeypatch.setattr(database.client,'start_session',lambda:FakeSession())
    replacements={'db':fake_db,'login':login,'hr_backend.hr_dashboard':hr,'services.activity_audit':audit,'hr_backend.hr_employees_directory':directory,'hr_backend.hr_roles':roles}
    with patch.dict(sys.modules,replacements):
        flow=load('hr_backend.branch_recruitment','hr_backend/branch_recruitment.py')
        employee_add=load('hr_backend.hr_employee_add','hr_backend/hr_employee_add.py')
        recruits=load('hr_backend.hr_recruits','hr_backend/hr_recruits.py')
        register=load('_recruit_register','register.py');register_manager=load('_recruit_register_manager','register_manager.py')
        root=Path(__file__).resolve().parents[1];app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'));app.secret_key='recruit-tests';app.testing=True
        app.register_blueprint(flow.recruitment_flow_bp);app.register_blueprint(hr.hr_bp);app.register_blueprint(register.register_bp);app.register_blueprint(register_manager.register_manager_bp)
        app.context_processor(lambda:{'identity':login.get_current_identity()});app.url_build_error_handlers.append(lambda error,endpoint,values:'/_unused/'+endpoint)
        client=app.test_client();ids={key:ObjectId() for key in ('manager','other_manager','hr','other_hr','admin','executive','existing_agent')}
        for key,id in ids.items():
            role='manager' if key=='other_manager' else 'hr' if key=='other_hr' else 'agent' if key=='existing_agent' else key
            branch='Tema' if key=='other_manager' else 'Accra' if role in ('manager','agent') else 'HQ'
            database.users.insert_one({'_id':id,'name':key.replace('_',' ').title(),'username':key,'role':role,'branch':branch,'status':'Active','is_active':True,'phone':'024000'+str(list(ids).index(key)).zfill(4)})
        def signin(key='manager'):
            role=database.users.find_one({'_id':ids[key]})['role']
            with client.session_transaction() as state:state.clear();state.update(role=role,user_id=str(ids[key]),name=key.replace('_',' ').title());state[role+'_id']=str(ids[key])
        signin()
        yield client,database,signin,ids,flow,recruits

def candidate(**changes):return {'request_id':str(uuid4()),'full_name':'Candidate One','phone':'0241234567','email':'candidate@example.com','position_applied':'Sales agent','branch':'Forged branch','location':'Accra','note':'Walk-in applicant',**changes}


def test_hr_review_status_persists_filters_and_preserves_workflow(recruit_flow):
    client, database, signin, ids, *_ = recruit_flow
    candidate_id = create(recruit_flow)
    assert action(recruit_flow, candidate_id, 'review_status', status='Reviewed', notes='Checked CV').status_code == 403
    signin('hr')
    for version, status in enumerate(('Reviewed', 'Shortlisted')):
        result = action(recruit_flow, candidate_id, 'review_status', status=status, notes='CV assessment complete', expected_version=version)
        assert result.status_code == 200, result.json
        assert result.json['candidate']['status'] == status
        assert result.json['candidate']['stage'] == 'new'
        assert client.get('/api/recruitment/candidates?status=' + status).json['total'] == 1
    saved = database.hr_recruits.find_one({'_id': ObjectId(candidate_id)})
    assert saved['reviewed_by']['user_id'] == str(ids['hr'])
    assert saved['history'][-1]['details']['status'] == 'Shortlisted'
    assert action(recruit_flow, candidate_id, 'review_status', status='Reviewed', notes='Stale', expected_version=0).status_code == 409
    assert action(recruit_flow, candidate_id, 'review_status', status='Hired', notes='Bypass').status_code == 400
    assert action(recruit_flow, candidate_id, 'review_status', status='Reviewed', notes='').status_code == 400
    assert action(recruit_flow, candidate_id, 'approve', notes='').status_code == 400
    assert client.get('/api/recruitment/candidates?status=invalid').status_code == 400


def test_hr_can_approve_application_and_onboard_without_branch_interviews(recruit_flow):
    client, database, signin, ids, *_ = recruit_flow
    candidate_id = create(recruit_flow)
    assert action(recruit_flow, candidate_id, 'approve', notes='Manager cannot approve as HR').status_code == 403
    signin('hr')
    assert action(recruit_flow, candidate_id, 'onboard', **onboarding(recruit_flow)).status_code == 403
    result = action(recruit_flow, candidate_id, 'approve', notes='CV reviewed and candidate accepted by HR', expected_version=0)
    assert result.status_code == 200, result.json
    assert result.json['candidate']['stage'] == 'approved'
    assert result.json['candidate']['allowed_actions'] == ['onboard']
    result = action(recruit_flow, candidate_id, 'onboard', expected_version=1, **onboarding(recruit_flow))
    assert result.status_code == 200, result.json
    employee = database.users.find_one({'_id': ObjectId(result.json['employee_id'])})
    assert employee['name'] == 'Candidate One'
    assert employee['status'] == 'Active' and employee['is_active'] is True
    assert employee['manager_id'] == ids['manager']
    assert employee['recruitment_approval']['decided_by']['user_id'] == str(ids['hr'])
    assert database.hr_recruits.find_one({'_id': ObjectId(candidate_id)})['stage'] == 'onboarded'


def test_candidate_cv_upload_access_validation_and_history(recruit_flow, monkeypatch):
    client, database, signin, ids, flow, recruits = recruit_flow
    candidate_id = create(recruit_flow)
    url = '/api/recruitment/candidates/' + candidate_id + '/cv'
    signin('other_manager')
    assert client.post(url, data={'expected_version': '0'}).status_code == 404
    signin('admin')
    assert client.post(url, data={'expected_version': '0'}).status_code == 403
    signin('hr')
    assert client.post(url, data={'expected_version': '0'}).status_code == 400
    assert client.post(url, data={'expected_version': '0', 'cv_image': (io.BytesIO(b'bad'), 'cv.exe')}).status_code == 400
    uploads = []
    def upload(file):
        uploads.append(file.read())
        return 'https://example.com/new-cv.png', 'uploaded-cv', None
    monkeypatch.setattr(recruits, '_upload_cv_to_cloudflare', upload)
    result = client.post(url, data={'expected_version': '0', 'cv_image': (io.BytesIO(b'cv image bytes'), 'cv.png')})
    assert result.status_code == 200, result.json
    assert uploads == [b'cv image bytes']
    doc = database.hr_recruits.find_one({'_id': ObjectId(candidate_id)})
    assert doc['cv_image_id'] == 'uploaded-cv'
    assert doc['history'][-1]['action'] == 'Candidate CV uploaded'
    assert client.get('/api/recruitment/candidates/' + candidate_id).json['candidate']['cv_image_url'] == 'https://example.com/new-cv.png'
    assert client.post(url, data={'expected_version': '0'}).status_code == 409
    assert len(uploads) == 1
def create(flow,**changes):
    result=flow[0].post('/api/recruitment/candidates',json=candidate(**changes));assert result.status_code==201,result.json;return result.json['candidate']['id']
def action(flow,id,kind,**changes):
    return flow[0].post('/api/recruitment/candidates/'+id+'/action',json={'action':kind,**changes})
def interview(flow,id,kind='branch_interview',**changes):return action(flow,id,kind,attended=True,interview_date=date.today().isoformat(),outcome='Suitable' if kind=='branch_interview' else 'Recommended',notes='Interview attended and assessed',**changes)
def approved(flow):
    id=create(flow);assert interview(flow,id).status_code==200;assert action(flow,id,'submit_hr',hr_owner_id=str(flow[3]['hr'])).status_code==200;flow[2]('hr');assert interview(flow,id,'hr_interview').status_code==200;assert action(flow,id,'approve',notes='Suitable for employment').status_code==200;return id
def onboarding(flow,**changes):return {'branch':'Accra','role':'agent','manager_id':str(flow[3]['manager']),'username':'new_agent','password':'InitialStrong123','start_date':date.today().isoformat(),'onboarding_confirmed':True,**changes}

def test_branch_to_hr_pipeline_activation_and_branch_totals(recruit_flow):
    client,db,signin,ids,*_=recruit_flow
    before=client.get('/api/recruitment/summary').json;assert next(b['active_staff'] for b in before['branches'] if b['branch']=='Accra')==2
    id=create(recruit_flow);doc=db.hr_recruits.find_one({});assert doc['recruiting_branch']=='Accra' and db.users.count_documents({})==7
    assert action(recruit_flow,id,'submit_hr',hr_owner_id=str(ids['hr'])).status_code==403
    assert interview(recruit_flow,id).json['candidate']['stage']=='branch_interview_completed'
    assert action(recruit_flow,id,'submit_hr',hr_owner_id=str(ids['hr'])).json['candidate']['stage']=='awaiting_hr_interview'
    signin('hr');summary=client.get('/api/recruitment/summary').json;assert summary['counts']['awaiting_hr_interview']==1 and summary['unread_count']==1
    assert action(recruit_flow,id,'approve',notes='').status_code==400
    assert interview(recruit_flow,id,'hr_interview').json['candidate']['stage']=='pending_hr_approval'
    assert action(recruit_flow,id,'approve',notes='Approved following HR interview').json['candidate']['stage']=='approved'
    result=action(recruit_flow,id,'onboard',**onboarding(recruit_flow));assert result.status_code==200,result.json
    employee=db.users.find_one({'_id':ObjectId(result.json['employee_id'])});assert employee['is_active'] and employee['branch']=='Accra' and employee['manager_id']==ids['manager']
    assert employee['recruitment_approval']['decided_by']['role']=='hr'
    assert next(b['active_staff'] for b in client.get('/api/recruitment/summary').json['branches'] if b['branch']=='Accra')==3
    assert action(recruit_flow,id,'onboard',**onboarding(recruit_flow)).json['employee_id']==result.json['employee_id']
    assert db.users.count_documents({'recruitment_candidate_id':ObjectId(id)})==1
    signin();assert client.get('/api/recruitment/summary').json['counts']['onboarded']==1

@pytest.mark.parametrize('key',['manager','other_manager','admin','executive'])
def test_only_hr_can_interview_approve_or_onboard(recruit_flow,key):
    id=approved(recruit_flow);recruit_flow[2](key)
    for kind in (('hr_interview','approve') if key == 'manager' else ('hr_interview','approve','onboard')):
        result=action(recruit_flow,id,kind,notes='Bypass',**onboarding(recruit_flow));assert result.status_code in (403,404)
    assert recruit_flow[1].users.count_documents({})==7


def test_manager_onboards_own_branch_without_main_hr_approval_with_attribution(recruit_flow):
    client, database, signin, ids, *_ = recruit_flow
    candidate_id = create(recruit_flow)
    assert action(recruit_flow, candidate_id, 'onboard', **onboarding(recruit_flow)).status_code == 403
    assert interview(recruit_flow, candidate_id).status_code == 200
    result = action(recruit_flow, candidate_id, 'onboard', **onboarding(recruit_flow))
    assert result.status_code == 200, result.json
    employee = database.users.find_one({'_id': ObjectId(result.json['employee_id'])})
    assert employee['is_active'] and employee['branch'] == 'Accra'
    assert employee['recruitment_approval'] is None
    assert employee['hr_recorded_by']['user_id'] == str(ids['manager'])
    assert employee['hr_recorded_by']['role'] == 'manager'
    logs = list(database.hr_record_activity.find({'collection': 'users', 'operation': 'created'}))
    assert len(logs) == 1 and logs[0]['record_id'] == str(employee['_id'])
    signin('hr')
    assert client.get('/api/recruitment/candidates/' + candidate_id).json['candidate']['stage'] == 'onboarded'
    assert client.get('/api/recruitment/summary').json['unread_count'] == 1
    signin()
    assert action(recruit_flow, candidate_id, 'onboard', **onboarding(recruit_flow)).json['employee_id'] == str(employee['_id'])
    assert database.hr_record_activity.count_documents({'collection': 'users', 'operation': 'created'}) == 1


@pytest.mark.parametrize('change', [{'branch':'Tema'}, {'role':'executive'}, {'role':'hr'}, {'role':'manager'}, {'role':'admin'}, {'role':'inventory'}, {'role':'accounting'}, {'role':'customer_support'}])
def test_manager_onboarding_cannot_escape_branch_or_create_privileged_roles(recruit_flow, change):
    candidate_id = create(recruit_flow)
    interview(recruit_flow, candidate_id)
    assert action(recruit_flow, candidate_id, 'onboard', **onboarding(recruit_flow, **change)).status_code == 403
    assert recruit_flow[1].users.count_documents({}) == 7


def test_failed_manager_onboarding_rolls_back_employee_and_activity(recruit_flow, monkeypatch):
    _, database, _, _, flow, *_ = recruit_flow
    candidate_id = create(recruit_flow)
    interview(recruit_flow, candidate_id)
    before = database.hr_record_activity.count_documents({})
    def fail(*args, **kwargs):
        raise ValueError('Injected persistence failure')
    monkeypatch.setattr(flow, 'persist', fail)
    result = action(recruit_flow, candidate_id, 'onboard', **onboarding(recruit_flow))
    assert result.status_code == 400
    assert database.users.count_documents({'recruitment_candidate_id': ObjectId(candidate_id)}) == 0
    assert database.hr_record_activity.count_documents({}) == before

def test_manager_branch_scope_and_forced_branch(recruit_flow):
    id=create(recruit_flow);client,db,signin,ids,*_=recruit_flow;signin('other_manager')
    assert client.get('/api/recruitment/candidates').json['total']==0
    assert client.get('/api/recruitment/candidates/'+id).status_code==404
    assert interview(recruit_flow,id).status_code==404
    assert client.get('/api/recruitment/branch-staff?branch=Accra').status_code==403
    assert client.get('/api/recruitment/options').json['branches']==['Tema']

def test_bad_interviews_and_unsuitable_branch_outcome(recruit_flow):
    id=create(recruit_flow)
    for values in ({'attended':False},{'interview_date':(date.today()+timedelta(days=1)).isoformat()},{'notes':''},{'outcome':'Approved'}):
        payload={'attended':True,'interview_date':date.today().isoformat(),'outcome':'Suitable','notes':'Interview',**values};assert action(recruit_flow,id,'branch_interview',**payload).status_code==400
    result=action(recruit_flow,id,'branch_interview',attended=True,interview_date=date.today().isoformat(),outcome='Unsuitable',notes='Not ready for role');assert result.json['candidate']['stage']=='rejected'
    assert action(recruit_flow,id,'submit_hr').status_code==403
    assert recruit_flow[0].get('/api/recruitment/summary').json['counts']['rejected']==1

def test_hr_decision_requires_notes_and_rejection_has_actor(recruit_flow):
    id=create(recruit_flow);interview(recruit_flow,id);action(recruit_flow,id,'submit_hr');recruit_flow[2]('hr')
    result=action(recruit_flow,id,'hr_interview',attended=True,interview_date=date.today().isoformat(),outcome='Not recommended',notes='Further qualification needed');assert result.json['candidate']['stage']=='pending_hr_approval'
    assert action(recruit_flow,id,'approve',notes='').status_code==400
    assert action(recruit_flow,id,'reject',notes='Not suitable following HR review').json['candidate']['hr_decision']['decided_by']['role']=='hr'

def test_create_retry_duplicate_phone_and_search_pagination(recruit_flow):
    client,db,*_=recruit_flow;data=candidate();first=client.post('/api/recruitment/candidates',json=data);assert first.status_code==201
    assert client.post('/api/recruitment/candidates',json=data).json['candidate']['id']==first.json['candidate']['id']
    assert client.post('/api/recruitment/candidates',json=candidate()).status_code==400
    assert client.get('/api/recruitment/candidates?stage=new&search=Candidate').json['total']==1
    assert client.get('/api/recruitment/candidates?stage=approved').json['total']==0
    assert client.get('/api/recruitment/candidates?stage=bogus').status_code==400
    assert client.get('/api/recruitment/candidates?search=%5B').status_code==200

@pytest.mark.parametrize('changes',[{'onboarding_confirmed':False},{'branch':'Unknown'},{'manager_id':'bad'},{'username':'invalid space'},{'password':'short'},{'start_date':(date.today()+timedelta(days=1)).isoformat()},{'role':'unknown'}])
def test_onboarding_validation_creates_no_employee(recruit_flow,changes):
    id=approved(recruit_flow);assert action(recruit_flow,id,'onboard',**onboarding(recruit_flow,**changes)).status_code==400
    assert recruit_flow[1].users.count_documents({})==7 and recruit_flow[1].hr_recruits.find_one({})['stage']=='approved'

def test_onboarding_transaction_rollback_and_unique_username(recruit_flow,monkeypatch):
    client,db,signin,ids,flow,*_=recruit_flow;id=approved(recruit_flow)
    assert action(recruit_flow,id,'onboard',**onboarding(recruit_flow,username='existing_agent')).status_code==400
    original=flow.persist
    def fail(*args,**kwargs):raise ValueError('Injected persistence failure')
    monkeypatch.setattr(flow,'persist',fail)
    assert action(recruit_flow,id,'onboard',**onboarding(recruit_flow)).status_code==400
    assert db.users.count_documents({})==7 and db.hr_recruits.find_one({})['stage']=='approved'
    monkeypatch.setattr(flow,'persist',original);assert action(recruit_flow,id,'onboard',**onboarding(recruit_flow)).status_code==200

def test_assigned_hr_notifications_private_and_readable(recruit_flow):
    client,db,signin,ids,*_=recruit_flow;id=create(recruit_flow);interview(recruit_flow,id);action(recruit_flow,id,'submit_hr',hr_owner_id=str(ids['hr']))
    signin('other_hr');assert client.get('/api/recruitment/summary').json['unread_count']==0
    signin('hr');notification=client.get('/api/recruitment/summary').json['notifications'][0];url='/api/recruitment/candidates/'+id+'/notifications/'+notification['id']+'/read'
    assert client.post(url,json={}).status_code==200;assert client.get('/api/recruitment/summary').json['unread_count']==0
    signin('other_hr');assert client.post(url,json={}).status_code==404

def test_public_application_requires_branch_manager_interview_and_legacy_no_shortcut(recruit_flow,monkeypatch):
    client,db,signin,ids,flow,recruits=recruit_flow
    monkeypatch.setattr(recruits,'_upload_cv_to_cloudflare',lambda file:('https://example.com/cv.png','cv-id',None))
    result=client.post('/hr/recruits/apply',data={'full_name':'Public Candidate','phone':'0247654321','email':'public@example.com','location':'Accra','position_applied':'Sales'});assert result.status_code==200
    doc=db.hr_recruits.find_one({});id=str(doc['_id']);signin('hr');assert client.get('/api/recruitment/summary').json['unread_count']==1
    assert action(recruit_flow,id,'approve',notes='').status_code==400
    assert action(recruit_flow,id,'assign_recruiting_branch',branch='Accra').status_code==200
    assert client.post('/hr/recruits/'+id+'/status',json={'status':'Hired'}).status_code==409
    signin('manager');assert client.get('/api/recruitment/candidates').json['total']==1
    assert client.post('/register',data={'name':'Shortcut'}).status_code==400
    signin('hr');assert client.post('/hr/employees/add',headers={'X-Requested-With':'XMLHttpRequest'},data={'name':'Shortcut'}).status_code==409
    signin('admin');assert client.post('/hr/employees/add',data={'name':'Shortcut'}).status_code==403
    assert client.post('/register_manager',data={'name':'Shortcut'}).status_code==409
    assert client.delete('/hr/recruits/'+id).status_code==409
    assert db.users.count_documents({})==7

def test_stale_action_and_rejected_audit_preserved(recruit_flow):
    id=create(recruit_flow);assert interview(recruit_flow,id).status_code==200
    assert action(recruit_flow,id,'submit_hr',expected_version=0).status_code==409
    recruit_flow[2]('hr');result=action(recruit_flow,id,'reject',notes='HR reviewed initial result');assert result.json['candidate']['stage']=='rejected'
    assert len(result.json['candidate']['history'])==3

def test_branch_assignment_only_after_hr_approval_and_receiving_manager_visibility(recruit_flow):
    client,db,signin,ids,*_=recruit_flow;id=approved(recruit_flow)
    result=action(recruit_flow,id,'onboard',**onboarding(recruit_flow,branch='Tema',manager_id=str(ids['other_manager'])));assert result.status_code==200
    doc=db.hr_recruits.find_one({});assert doc['recruiting_branch']=='Accra' and doc['assigned_branch']=='Tema'
    signin('other_manager');assert client.get('/api/recruitment/candidates/'+id).status_code==200
    assert client.get('/api/recruitment/summary').json['unread_count']==1
    assert len(client.get('/api/recruitment/branch-staff?branch=Tema').json['employees'])==2

def test_pages_and_legacy_stage_labels(recruit_flow):
    client,db,signin,*_=recruit_flow
    for path in ('/manager/recruitment','/manager/recruitment/register','/manager/recruitment/interviews'):assert client.get(path).status_code==200
    db.hr_recruits.insert_one({'full_name':'Old Hired','status':'Hired','phone':'0245555555'})
    signin('hr');assert client.get('/hr/recruits').status_code==200
    assert client.get('/api/recruitment/candidates?stage=legacy_hired').json['candidates'][0]['stage']=='legacy_hired'
    assert client.get('/api/recruitment/summary').json['counts']['onboarded']==0
    with client.session_transaction() as state:state.clear()
    assert client.get('/api/recruitment/candidates').status_code==401
