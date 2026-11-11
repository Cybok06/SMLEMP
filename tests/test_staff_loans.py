import importlib.util
import sys
import types
from pathlib import Path
from uuid import uuid4
from datetime import date, timedelta
from unittest.mock import patch
import mongomock
import pytest
from bson import ObjectId
from flask import Flask, session

@pytest.fixture
def loan_flow():
    database=mongomock.MongoClient().db
    fake_db=types.ModuleType('db');fake_db.db=database
    login_module=types.ModuleType('login')
    login_module.get_current_identity=lambda: {'is_authenticated':bool(session.get('user_id')), 'user_id':session.get('user_id'), 'role':session.get('role'), 'name':'Recorder'}
    spec=importlib.util.spec_from_file_location('_staff_loans_test',Path('hr_backend/staff_loans.py'))
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'db':fake_db,'login':login_module}):spec.loader.exec_module(module)
    root=Path(__file__).resolve().parents[1]
    app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'));app.secret_key='test';app.testing=True;app.register_blueprint(module.staff_loans_bp)
    client=app.test_client();agent,manager,admin=[ObjectId() for _ in range(3)]
    database.users.insert_many([{'_id':agent,'name':'Agent One','role':'agent'},{'_id':manager,'name':'Manager Two','role':'manager'},{'_id':admin,'name':'Admin','role':'admin'}])
    def signin(role='hr',user=admin):
        with client.session_transaction() as state:state.clear();state.update(role=role,user_id=str(user))
    signin()
    return client,database,signin,agent,manager,admin

def payload(agent,**changes):
    return {'request_id':str(uuid4()),'employee_id':str(agent),'kind':'Shared expense','total':'1200.50','company':'200.25','reason':'Company paid training','issued_date':(date.today()-timedelta(days=5)).isoformat(),'due_date':(date.today()-timedelta(days=1)).isoformat(),**changes}

def pay(**changes):
    return {'request_id':str(uuid4()),'amount':'100.25','date':date.today().isoformat(),'method':'Payroll deduction','reference':'Payroll October',**changes}

def test_split_and_immutable_original_figures_with_payment_ledger(loan_flow):
    client,db,login,agent,*_=loan_flow
    result=client.post('/api/staff-loans',json=payload(agent,principal=999999));assert result.status_code==201
    loan=result.json['loan'];assert loan['principal']==1000.25 and loan['remaining']==1000.25 and loan['status']=='Overdue'
    repayment=pay();result=client.post('/api/staff-loans/'+loan['id']+'/payments',json=repayment)
    assert result.status_code==200 and result.json['loan']['remaining']==900
    assert result.json['loan']['principal']==1000.25 and result.json['loan']['payments'][0]['recorded_by']['role']=='hr'
    assert client.post('/api/staff-loans/'+loan['id']+'/payments',json=repayment).json['loan']['remaining']==900
    assert client.post('/api/staff-loans/'+loan['id']+'/payments',json=pay(amount=900)).json['loan']['status']=='Settled'
    assert db.staff_loans.find_one({})['principal_cents']==100025

@pytest.mark.parametrize('changes',[{'total':0},{'company':1300},{'total':'NaN'},{'company':-1},{'total':'1.001'},{'kind':'Other'},{'reason':''},{'due_date':'bad'},{'employee_id':'bad'},{'issued_date':(date.today()+timedelta(days=1)).isoformat()}])
def test_invalid_debt_rejected(loan_flow,changes):
    client,db,_,agent,*_=loan_flow
    assert client.post('/api/staff-loans',json=payload(agent,**changes)).status_code==400
    assert db.staff_loans.count_documents({})==0

def test_manual_identity_and_creation_retry(loan_flow):
    client,db,_,agent,*_=loan_flow
    data=payload(agent,employee_id=None,manual=True,staff={'name':'Manual Staff','phone':'0241234567','staff_code':'MAN-1','branch':'Accra'})
    first=client.post('/api/staff-loans',json=data).json['loan'];second=client.post('/api/staff-loans',json=data).json['loan']
    assert first['id']==second['id'] and first['manual']
    data['request_id']=str(uuid4());client.post('/api/staff-loans',json=data)
    assert db.staff_loan_manual_staff.count_documents({})==1 and db.staff_loans.count_documents({})==2

@pytest.mark.parametrize('role',['hr','admin','executive'])
def test_authorised_roles_can_record_and_read(loan_flow,role):
    client,db,login,agent,*_=loan_flow;login(role)
    assert client.post('/api/staff-loans',json=payload(agent)).status_code==201
    assert len(client.get('/api/staff-loans').json['loans'])==1

@pytest.mark.parametrize('role',['agent','manager'])
def test_employee_scope_and_read_only(loan_flow,role):
    client,db,login,agent,manager,_=loan_flow
    for employee in (agent,manager):client.post('/api/staff-loans',json=payload(employee))
    employee=agent if role=='agent' else manager;login(role,employee)
    loans=client.get('/api/staff-loans?employee_id='+str(manager if role=='agent' else agent)).json['loans']
    assert len(loans)==1 and loans[0]['employee_id']==str(employee)
    assert client.post('/api/staff-loans',json=payload(employee)).status_code==403
    assert client.post('/api/staff-loans/'+loans[0]['id']+'/payments',json=pay()).status_code==403
    assert client.get('/api/staff-loans/employees').status_code==403

@pytest.mark.parametrize('changes',[{'amount':0},{'amount':-10},{'amount':'1000.26'},{'date':'2000-01-01'},{'date':(date.today()+timedelta(days=1)).isoformat()},{'method':'Invalid'},{'reference':''}])
def test_payment_validation(loan_flow,changes):
    client,db,_,agent,*_=loan_flow
    loan=client.post('/api/staff-loans',json=payload(agent)).json['loan']
    assert client.post('/api/staff-loans/'+loan['id']+'/payments',json=pay(**changes)).status_code==400
    assert db.staff_loans.find_one({})['remaining_cents']==100025

def test_company_funded_and_anonymous_scope(loan_flow):
    client,db,login,agent,*_=loan_flow
    loan=client.post('/api/staff-loans',json=payload(agent,total=200,company=200)).json['loan'];assert loan['principal']==0 and loan['status']=='Settled'
    login('agent',agent);assert client.get('/api/staff-loans/mine').json['summary']['remaining']==0
    with client.session_transaction() as state:state.clear()
    assert client.get('/api/staff-loans').status_code==401
    assert client.get('/staff-loans').status_code==401

def test_concurrent_balance_change_is_not_overwritten(loan_flow,monkeypatch):
    client,db,_,agent,*_=loan_flow;loan=client.post('/api/staff-loans',json=payload(agent)).json['loan']
    original=db.staff_loans.update_one
    def race(query,update,*args,**kwargs):
        original({'_id':ObjectId(loan['id'])},{'$inc':{'version':1,'remaining_cents':-100}})
        return original(query,update,*args,**kwargs)
    monkeypatch.setattr(db.staff_loans,'update_one',race)
    assert client.post('/api/staff-loans/'+loan['id']+'/payments',json=pay()).status_code==409
    assert db.staff_loans.find_one({})['remaining_cents']==99925


def test_manual_reference_cannot_be_reused_for_a_different_person(loan_flow):
    client,db,_,agent,*_=loan_flow
    data=payload(agent,employee_id=None,manual=True,staff={'name':'Manual Staff','phone':'0241234567','staff_code':'MAN-1'})
    assert client.post('/api/staff-loans',json=data).status_code==201
    data['request_id']=str(uuid4());data['staff']['name']='Different Person'
    assert client.post('/api/staff-loans',json=data).status_code==400
    assert db.staff_loans.count_documents({})==1
