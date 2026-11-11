"""Branch interviews, HR decisions and transactional employee onboarding."""
from datetime import datetime, date, timezone
from uuid import UUID, uuid4
import re
import bcrypt
from bson import ObjectId
from flask import Blueprint, abort, jsonify, render_template, request
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import get_current_identity
from services.work_time import ROLES
from services.hr_record_audit import BRANCH_STAFF_ROLES

recruitment_flow_bp=Blueprint("recruitment_flow",__name__)
STAGES={"new":"New candidates","branch_interview_completed":"Branch interviews completed","awaiting_hr_interview":"Awaiting HR interview","pending_hr_approval":"Pending HR approval","approved":"Approved","rejected":"Rejected","onboarded":"Onboarded"}
NEXT={"new":"Branch interview" ,"branch_interview_completed":"Submit suitable candidate to HR","awaiting_hr_interview":"Attend and record HR interview","pending_hr_approval":"HR approval decision","approved":"Assign branch and onboard","rejected":"No further action","onboarded":"View employee profile","legacy_hired":"Legacy hired record; review existing employee history"}
ACCESS={"manager","hr","admin","executive"}

def now():return datetime.now(timezone.utc)
def actor(identity):return {"user_id":str(identity["user_id"]),"name":identity.get("name") or "Staff","role":identity["role"]}
def identity():
    user=get_current_identity()
    if not user.get("is_authenticated") or not user.get("user_id"):abort(401)
    if user.get("role") not in ACCESS:abort(403)
    return user

def require_hr(identity):
    if identity["role"]!="hr":abort(403,description="HR must conduct this recruitment action.")

def oid(value):
    try:return ObjectId(value)
    except Exception:raise ValueError("Invalid candidate or staff identifier.")

def clean(value,label,required=False,limit=1000):
    if not isinstance(value,str):raise ValueError(label+" must be text.")
    value=value.strip()
    if len(value)>limit or (required and not value):raise ValueError(label+" is required and must be within "+str(limit)+" characters.")
    return value

def payload():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise ValueError("Enter a valid recruitment record.")
    return data

def stage(doc):
    if doc.get("stage"):return doc["stage"]
    if doc.get("status")=="Rejected":return "rejected"
    if doc.get("status")=="Hired":return "legacy_hired"
    return "new"

def active_query():
    return {"is_deleted":{"$ne":True},"is_active":{"$ne":False},"account_locked":{"$ne":True},"login_blocked":{"$ne":True},"employment_status":{"$ne":"Exited"},"status":{"$nin":["Inactive","inactive","Not Active","Not active","not active","Disabled","disabled","Exited"]},"is_role_profile":{"$ne":True}}

def managers():
    return list(db.users.find({**active_query(),"role":"manager","branch":{"$nin":[None,""]}},{"name":1,"branch":1,"role":1}))

def options():
    people=managers()
    return {"branches":sorted({p["branch"] for p in people if isinstance(p.get("branch"),str)}),"managers":[{"id":str(p["_id"]),"name":p.get("name","Manager"),"branch":p["branch"]} for p in people],"hr_staff":[{"id":str(p["_id"]),"name":p.get("name") or p.get("username","HR")} for p in db.users.find({**active_query(),"role":"hr"},{"name":1,"username":1})],"roles":list(ROLES)}

def branch_for(identity):
    user=db.users.find_one({"_id":oid(identity["user_id"]),"role":"manager"})
    if not user or not user.get("branch"):abort(403,description="Your manager account must have a branch.")
    return user["branch"]

def scope(identity):
    if identity["role"]=="manager":
        branch=branch_for(identity)
        return {"$or":[{"recruiting_branch":branch},{"assigned_branch":branch,"stage":"onboarded"}]}
    return {}

def find(candidate_id,identity,session=None):
    doc=db.hr_recruits.find_one({"_id":oid(candidate_id),**scope(identity)},session=session)
    if not doc:abort(404)
    return doc

def stage_query(key):
    if key=="new":return {"$or":[{"stage":"new"},{"stage":{"$exists":False},"status":{"$nin":["Rejected","Hired"]}}]}
    if key=="rejected":return {"$or":[{"stage":"rejected"},{"stage":{"$exists":False},"status":"Rejected"}]}
    if key=="legacy_hired":return {"stage":{"$exists":False},"status":"Hired"}
    return {"stage":key}

def notice(title,target_role,target_id=None,stamp=None):
    return {"id":str(uuid4()),"title":title,"role":target_role,"user_id":target_id,"at":stamp or now().isoformat(),"read_by":[],"resolved_at":None}

def notifications_for(doc,identity):
    return [n for n in doc.get("notifications",[]) if n["role"]==identity["role"] and (not n.get("user_id") or n["user_id"]==str(identity["user_id"])) and not n.get("resolved_at") and str(identity["user_id"]) not in n.get("read_by",[])]

def actions(doc,identity):
    current=stage(doc)
    if identity["role"]=="manager":
        if current=="new":return ["branch_interview"]
        if current=="branch_interview_completed":return ["onboard","branch_interview","submit_hr"] if doc.get("branch_interview",{}).get("outcome")=="Suitable" else ["branch_interview"]
        if current in {'awaiting_hr_interview', 'pending_hr_approval', 'approved'} and doc.get('branch_interview', {}).get('outcome') == 'Suitable':return ['onboard']
        return []
    if identity["role"]!="hr":return []
    if current=="new":return ["approve","reject","review_status","assign_recruiting_branch"]
    if current=="branch_interview_completed":return ["approve","reject","review_status"]
    if current=="awaiting_hr_interview":return ["hr_interview","approve","reject","schedule_hr","review_status"]
    if current=="pending_hr_approval":return ["approve","reject"]
    if current=="approved":return ["onboard"]
    return []

def public(doc,identity):
    result={k:v for k,v in doc.items() if k not in {"_id","request_id","notifications"}}
    result["id"]=str(doc["_id"]);result["stage"]=stage(doc);result["stage_label"]=STAGES.get(stage(doc),"Legacy hired");result["next_action"]=NEXT.get(stage(doc),"Review record")
    if stage(doc)=="new" and not doc.get("recruiting_branch"):result["next_action"]="HR: assign recruiting branch"
    if stage(doc)=="branch_interview_completed" and doc.get("branch_interview",{}).get("outcome")!="Suitable":result["next_action"]="Branch: review or repeat interview"
    result["allowed_actions"]=actions(doc,identity)
    if identity['role']=='hr' and stage(doc) not in {'approved','rejected','onboarded','legacy_hired'}:
        result['next_action']='Review application and approve or reject with decision notes; interviews can be recorded when needed.'
    if identity['role'] == 'manager' and 'onboard' in result['allowed_actions']:
        result['next_action'] = 'Complete branch onboarding; main HR can review the activity log'
    result["notifications"]=notifications_for(doc,identity)
    for key in ("submitted_at","updated_at"):
        if isinstance(result.get(key),datetime):result[key]=result[key].isoformat()
    for key in ("employee_id",):
        if result.get(key):result[key]=str(result[key])
    return result

def event(action,identity,details):return {"id":str(uuid4()),"action":action,"actor":actor(identity),"at":now().isoformat(),"details":details}

def persist(doc,fields,entry,new_notice=None,session=None):
    stamp=now();version=doc.get("version")
    notices=[{**n,"resolved_at":n.get("resolved_at") or stamp.isoformat()} for n in doc.get("notifications",[])]
    if new_notice:notices.append(new_notice)
    fields={**fields,"updated_at":stamp,"notifications":notices}
    update={"$set":fields,"$inc":{"version":1},"$push":{"history":entry}}
    if fields.get("stage") in {"rejected","onboarded"}:update["$unset"]={"open_phone":""}
    result=db.hr_recruits.update_one({"_id":doc["_id"],"version":version if version is not None else {"$exists":False}},update,session=session)
    if not result.modified_count:raise ValueError("This candidate changed. Refresh before taking another action.")

@recruitment_flow_bp.errorhandler(ValueError)
def invalid(error):return jsonify(ok=False,message=str(error)),400
@recruitment_flow_bp.errorhandler(PyMongoError)
def unavailable(error):return jsonify(ok=False,message="Recruitment could not be saved. Refresh before retrying."),503

@recruitment_flow_bp.get("/manager/recruitment")
@recruitment_flow_bp.get("/manager/recruitment/register")
@recruitment_flow_bp.get("/manager/recruitment/interviews")
@recruitment_flow_bp.get("/admin/recruitment")
@recruitment_flow_bp.get("/executive/recruitment")
def page():
    user=identity()
    if request.path.split('/')[1]!=user['role']:abort(403)
    return render_template('recruitment_flow_page.html',recruitment_role=user['role'],recruitment_register=request.path.endswith('/register'),recruitment_interviews=request.path.endswith('/interviews'))

def hr_page():
    user=identity()
    context={'recruitment_role':user['role'],'active_page':'recruits','hr_content_template':'recruitment_flow_content.html'}
    if request.headers.get('X-Requested-With')=='XMLHttpRequest':return render_template('recruitment_flow_content.html',**context)
    return render_template('hr_pages/hr_shell.html',**context)

@recruitment_flow_bp.get('/api/recruitment/options')
def get_options():
    user=identity();data=options()
    if user['role']=='manager':
        branch=branch_for(user)
        data['branches']=[branch]
        data['managers']=[person for person in data['managers'] if person['branch']==branch]
        data['roles'] = sorted(BRANCH_STAFF_ROLES)
    return jsonify(ok=True,**data)

@recruitment_flow_bp.get('/api/recruitment/summary')
def overview():
    user=identity();query=scope(user);counts={key:db.hr_recruits.count_documents({'$and':[query,stage_query(key)]}) for key in STAGES}
    count_query={**active_query(),'status':{'$in':['Active','active']}}
    if user['role']=='manager':count_query['branch']=branch_for(user)
    branches={branch:0 for branch in options()['branches'] if user['role']!='manager' or branch==branch_for(user)}
    for employee in db.users.find(count_query,{'branch':1,'manager_id':1}):
        branch=employee.get('branch')
        if isinstance(branch,str) and branch:branches[branch]=branches.get(branch,0)+1
    notifications=[]
    for doc in db.hr_recruits.find(query,{'notifications':1,'full_name':1,'stage':1,'status':1}):
        for n in notifications_for(doc,user):notifications.append({**n,'candidate_id':str(doc['_id']),'candidate_name':doc.get('full_name','Candidate'),'stage':stage(doc)})
    notifications.sort(key=lambda n:n['at'],reverse=True)
    return jsonify(ok=True,counts=counts,stages=STAGES,branches=[{'branch':key,'active_staff':value} for key,value in sorted(branches.items())],notifications=notifications[:50],unread_count=len(notifications),role=user['role'])

@recruitment_flow_bp.get('/api/recruitment/candidates')
def candidate_list():
    user=identity();query=scope(user);filters=[query]
    key=request.args.get('stage','')
    if key:
        if key not in STAGES and key!='legacy_hired':raise ValueError('Invalid recruitment stage.')
        filters.append(stage_query(key))
    branch=request.args.get('branch','')
    if branch:filters.append({'recruiting_branch':branch})
    status=request.args.get('status','')
    if status:
        if status not in {'New','Reviewed','Shortlisted','Approved','Rejected','Hired'}:raise ValueError('Invalid application status.')
        filters.append({'status':status})
    search=request.args.get('search','').strip()[:100]
    if search:filters.append({'$or':[{field:{'$regex':re.escape(search),'$options':'i'}} for field in ('full_name','phone','position_applied')]})
    try:page=max(1,int(request.args.get('page','1')))
    except ValueError:raise ValueError('Invalid page.')
    query={'$and':filters};total=db.hr_recruits.count_documents(query)
    docs=db.hr_recruits.find(query).sort('submitted_at',-1).skip((page-1)*25).limit(25)
    return jsonify(ok=True,candidates=[public(d,user) for d in docs],total=total,page=page,pages=max(1,(total+24)//25))

@recruitment_flow_bp.get('/api/recruitment/candidates/<candidate_id>')
def candidate_detail(candidate_id):
    user=identity();return jsonify(ok=True,candidate=public(find(candidate_id,user),user))

@recruitment_flow_bp.post('/api/recruitment/candidates')
def create_candidate():
    user=identity()
    if user['role'] not in {'manager','hr'}:abort(403)
    data=payload()
    try:request_id=str(UUID(data.get('request_id')))
    except (ValueError,TypeError,AttributeError):raise ValueError('A valid request identifier is required.')
    db.hr_recruits.create_index('request_id',unique=True,partialFilterExpression={'request_id':{'$type':'string'}})
    existing=db.hr_recruits.find_one({'request_id':request_id})
    if existing:
        if existing.get('created_by',{}).get('user_id')!=str(user['user_id']):abort(403)
        return jsonify(ok=True,candidate=public(existing,user))
    branch=branch_for(user) if user['role']=='manager' else clean(data.get('branch',''),'Recruiting branch',True,150)
    people=[p for p in managers() if p['branch']==branch]
    if not people:raise ValueError('Choose a branch with an active manager.')
    responsible=next((p for p in people if str(p['_id'])==str(user['user_id'])),people[0])
    email=clean(data.get('email',''),'Email',False,150).lower()
    if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):raise ValueError('Enter a valid email address.')
    doc={'full_name':clean(data.get('full_name',''),'Candidate name',True,150),'phone':re.sub(r'[^0-9+]','',clean(data.get('phone',''),'Phone',True,60)),'email':email,'location':clean(data.get('location',''),'Location',False,150),'position_applied':clean(data.get('position_applied',''),'Position',True,150),'note':clean(data.get('note',''),'Notes'),'recruiting_branch':branch,'branch_manager_id':str(responsible['_id']),'branch_manager_name':responsible.get('name','Manager'),'stage':'new','status':'New','source':'branch' if user['role']=='manager' else 'hr','submitted_at':now(),'updated_at':now(),'version':0,'request_id':request_id,'created_by':actor(user)}
    if db.hr_recruits.find_one({'phone':doc['phone'],'stage':{'$nin':['rejected','onboarded']}}):raise ValueError('An open application already exists for this phone number. Review that candidate first.')
    if db.users.find_one({'phone':doc['phone']}):raise ValueError('An employee already uses this phone number.')
    doc['phone']=re.sub(r'[^0-9+]','',doc['phone'])
    if not re.fullmatch(r'\+?[0-9]{7,15}',doc['phone']):raise ValueError('Enter a valid phone number.')
    doc['open_phone']=doc['phone']
    db.hr_recruits.create_index('open_phone',unique=True,partialFilterExpression={'open_phone':{'$type':'string'}})
    doc['history']=[event('Candidate registered',user,{'branch':branch})];doc['notifications']=[notice('New candidate: conduct branch interview','manager',doc['branch_manager_id'])]
    try:doc['_id']=db.hr_recruits.insert_one(doc).inserted_id
    except DuplicateKeyError:
        doc=db.hr_recruits.find_one({'request_id':request_id})
        if not doc:raise ValueError('An open application already uses this phone number.')
    return jsonify(ok=True,candidate=public(doc,user)),201

@recruitment_flow_bp.post('/api/recruitment/candidates/<candidate_id>/action')
def candidate_action(candidate_id):
    user=identity();doc=find(candidate_id,user);data=payload();action=data.get('action')
    if action=='onboard' and stage(doc)=='onboarded' and user['role'] in {'hr', 'manager'}:
        return jsonify(ok=True,candidate=public(doc,user),employee_id=doc.get('employee_id'))
    if 'expected_version' in data and data['expected_version']!=doc.get('version',0):
        return jsonify(ok=False,message='Candidate changed. Refresh before recording an action.'),409
    if action not in actions(doc,user):abort(403,description='This role or recruitment stage cannot perform that action.')
    fields={};entry=None;alert=None
    if action=='review_status':
        require_hr(user)
        status=data.get('status')
        if status not in {'New','Reviewed','Shortlisted'}:raise ValueError('Choose New, Reviewed or Shortlisted. Use the interview and decision actions for approval or rejection.')
        notes=clean(data.get('notes',''),'Review notes',True)
        fields={'status':status,'reviewed_by':actor(user),'reviewed_at':now().isoformat()}
        entry=event('Application status updated',user,{'status':status,'notes':notes})
    elif action=='assign_recruiting_branch':
        require_hr(user);branch=clean(data.get('branch',''),'Branch',True,150);people=[p for p in managers() if p['branch']==branch]
        if not people:raise ValueError('Choose a branch with an active manager.')
        if doc.get('branch_interview'):raise ValueError('The recruiting branch cannot change after its interview.')
        manager=people[0];fields={'recruiting_branch':branch,'branch_manager_id':str(manager['_id']),'branch_manager_name':manager.get('name','Manager'),'stage':'new'}
        entry=event('Recruiting branch assigned',user,{'branch':branch});alert=notice('Candidate assigned: conduct branch interview','manager',str(manager['_id']))
    elif action in {'branch_interview','hr_interview'}:
        if data.get('attended') is not True:raise ValueError('Confirm that the candidate attended the interview.')
        try:interview_date=date.fromisoformat(data.get('interview_date'))
        except (ValueError,TypeError):raise ValueError('Enter a valid interview date.')
        if interview_date>date.today():raise ValueError('Completed interviews cannot be future-dated.')
        if action=='hr_interview' and interview_date.isoformat()<doc.get('branch_interview',{}).get('date',''):raise ValueError('The HR interview must take place on or after the branch interview.')
        notes=clean(data.get('notes',''),'Interview notes',True)
        outcome=data.get('outcome');choices={'Suitable','Unsuitable','Hold'} if action=='branch_interview' else {'Recommended','Not recommended','Hold'}
        if outcome not in choices:raise ValueError('Choose a valid interview outcome.')
        interview={'attended':True,'date':interview_date.isoformat(),'outcome':outcome,'notes':notes,'interviewer':actor(user),'recorded_at':now().isoformat()}
        fields[action]=interview
        if action=='branch_interview':
            fields.update(stage='rejected' if outcome=='Unsuitable' else 'branch_interview_completed',status='Rejected' if outcome=='Unsuitable' else 'Reviewed')
            if outcome!='Unsuitable':alert=notice('Branch interview recorded: '+('submit suitable candidate to HR' if outcome=='Suitable' else 'review interview outcome'),'manager',str(user['user_id']))
        else:
            require_hr(user);fields['hr_owner_id']=str(user['user_id']);fields['hr_owner_name']=user.get('name','HR')
            fields['stage']='pending_hr_approval' if outcome!='Hold' else 'awaiting_hr_interview'
            alert=notice('HR interview completed: recruitment decision required' if outcome!='Hold' else 'HR interview on hold: further review required','hr',str(user['user_id']))
        entry=event('Branch interview' if action=='branch_interview' else 'HR interview',user,interview)
    elif action=='submit_hr':
        if doc.get('branch_interview',{}).get('outcome')!='Suitable':raise ValueError('Only suitable interviewed candidates can be submitted to HR.')
        owner=data.get('hr_owner_id') or None;staff=options()['hr_staff']
        if owner and not any(p['id']==owner for p in staff):raise ValueError('Select an active HR staff member.')
        owner_name=next((p['name'] for p in staff if p['id']==owner),'HR team')
        fields={'stage':'awaiting_hr_interview','status':'Shortlisted','hr_owner_id':owner,'hr_owner_name':owner_name,'submitted_to_hr_at':now().isoformat(),'submitted_to_hr_by':actor(user)}
        entry=event('Submitted to HR',user,{'hr_owner_id':owner,'hr_owner_name':owner_name});alert=notice('Candidate submitted: schedule and conduct HR interview','hr',owner)
    elif action=='schedule_hr':
        require_hr(user)
        try:scheduled=datetime.fromisoformat(data.get('scheduled_at'))
        except (ValueError,TypeError):raise ValueError('Enter a valid scheduled interview time.')
        if scheduled.tzinfo is None:scheduled=scheduled.replace(tzinfo=timezone.utc)
        if scheduled<now():raise ValueError('Choose a future interview time.')
        schedule={'at':scheduled.isoformat(),'location':clean(data.get('interview_location',''),'Interview location',True,200),'scheduled_by':actor(user)}
        fields={'hr_schedule':schedule,'hr_owner_id':str(user['user_id']),'hr_owner_name':user.get('name','HR')};entry=event('HR interview scheduled',user,schedule);alert=notice('HR interview scheduled: candidate attendance and outcome required','hr',str(user['user_id']))
    elif action in {'approve','reject'}:
        require_hr(user);reason=clean(data.get('notes',''),'Decision notes',True)
        decision={'outcome':'Approved' if action=='approve' else 'Rejected','notes':reason,'at':now().isoformat(),'decided_by':actor(user)}
        fields={'stage':'approved' if action=='approve' else 'rejected','status':'Approved' if action=='approve' else 'Rejected','hr_decision':decision}
        entry=event('HR approval' if action=='approve' else 'HR rejection',user,decision)
        if action=='approve':fields.update(hr_owner_id=str(user['user_id']),hr_owner_name=user.get('name','HR'));alert=notice('Approved candidate: assign branch and complete onboarding','hr',str(user['user_id']))
        else:alert=notice('HR rejected candidate: '+doc.get('full_name','Candidate'),'manager',doc.get('branch_manager_id'))
    elif action=='onboard':return onboard(doc,user,data)
    persist(doc,fields,entry,alert)
    return jsonify(ok=True,candidate=public(find(candidate_id,user),user))

@recruitment_flow_bp.post('/api/recruitment/candidates/<candidate_id>/cv')
def candidate_cv(candidate_id):
    user=identity()
    if user['role'] not in {'hr','manager'}:abort(403)
    doc=find(candidate_id,user)
    try:version=int(request.form.get('expected_version',''))
    except (TypeError,ValueError):raise ValueError('Refresh the candidate before uploading a CV.')
    if version!=doc.get('version',0):return jsonify(ok=False,message='Candidate changed. Refresh before uploading a CV.'),409
    from hr_backend.hr_recruits import _upload_cv_to_cloudflare
    url,image_id,error=_upload_cv_to_cloudflare(request.files.get('cv_image'))
    if error:raise ValueError(error)
    persist(doc,{'cv_image_url':url,'cv_image_id':image_id},event('Candidate CV uploaded',user,{}))
    return jsonify(ok=True,candidate=public(find(candidate_id,user),user))


def onboard(doc,user,data):
    manager_onboarding = user['role'] == 'manager'
    if not manager_onboarding:require_hr(user)
    if data.get('onboarding_confirmed') is not True:raise ValueError('Confirm onboarding and employee activation.')
    branch=clean(data.get('branch',''),'Assigned branch',True,150);role=data.get('role')
    if manager_onboarding:
        if branch != branch_for(user):abort(403, description='Employees can only be registered in your own branch.')
        if role not in BRANCH_STAFF_ROLES:abort(403, description='Privileged account roles require central HR.')
    if role not in ROLES:raise ValueError('Select a valid employee role.')
    people=[p for p in managers() if p['branch']==branch]
    if not people:raise ValueError('Choose a branch with an active manager.')
    manager=next((p for p in people if str(p['_id'])==data.get('manager_id')),None)
    if role=='agent' and not manager:raise ValueError('Select an active manager from the assigned branch.')
    username=clean(data.get('username',''),'Username',True,100).lower()
    if not re.fullmatch(r'[a-z0-9_.-]+',username):raise ValueError('Username may contain letters, digits, dots, underscores and hyphens.')
    password=data.get('password')
    if not isinstance(password,str) or not password.strip() or len(password)<8 or len(password.encode('utf-8'))>72:raise ValueError('Use a password of at least 8 characters and at most 72 UTF-8 bytes.')
    try:start_date=date.fromisoformat(data.get('start_date'))
    except (ValueError,TypeError):raise ValueError('Enter a valid employment start date.')
    if start_date>date.today():raise ValueError('Activate an employee on or after their start date.')
    db.users.create_index('recruitment_candidate_id',unique=True,sparse=True)
    db.users.create_index('username',unique=True)
    employee_id=ObjectId();stamp=now()
    employee={'_id':employee_id,'name':doc['full_name'],'phone':doc['phone'],'email':doc.get('email',''),'location':doc.get('location',''),'position':doc.get('position_applied',''),'username':username,'password':bcrypt.hashpw(password.encode('utf-8'),bcrypt.gensalt()).decode('utf-8'),'role':role,'branch':branch,'status':'Active','is_active':True,'account_locked':False,'login_blocked':False,'employment_status':'Probation','start_date':start_date.isoformat(),'date_registered':stamp,'created_at':stamp,'updated_at':stamp,'assets':[],'recruit_id':doc['_id'],'recruitment_candidate_id':doc['_id'],'recruitment_stage':'Documentation Approved','recruitment_approval':doc.get('hr_decision'),'cv_image_url':doc.get('cv_image_url',''),'cv_image_id':doc.get('cv_image_id',''),'recruitment_source':{'type':doc.get('source','branch'),'branch':doc.get('recruiting_branch'),'manager_id':doc.get('branch_manager_id')},'recruitment_checklist':[]}
    if role=='agent':employee['manager_id']=manager['_id']
    assigned_manager=manager or people[0]
    entry=event('Employee onboarded and activated',user,{'employee_id':str(employee_id),'branch':branch,'role':role,'manager_id':str(assigned_manager['_id'])})
    def commit(session):
        current=db.hr_recruits.find_one({'_id':doc['_id']},session=session)
        existing=db.users.find_one({'recruitment_candidate_id':doc['_id']},session=session)
        if existing and stage(current)=='onboarded':return current
        if not current or current.get('version')!=doc.get('version'):raise ValueError('Candidate changed. Refresh before onboarding.')
        if manager_onboarding:
            if 'onboard' not in actions(current,user) or current.get('branch_interview',{}).get('attended') is not True:
                raise ValueError('Complete a suitable branch interview before onboarding.')
            if current.get('recruiting_branch') != branch:abort(403)
        elif stage(current) != 'approved' or current.get('hr_decision',{}).get('decided_by',{}).get('role')!='hr':
            raise ValueError('Final HR approval is required before onboarding.')
        if not db.users.find_one({**active_query(),'_id':assigned_manager['_id'],'role':'manager','branch':branch},session=session):raise ValueError('The assigned branch manager is no longer active. Refresh before onboarding.')
        if db.users.find_one({'$or':[{'username':username},{'phone':doc['phone']}]},session=session):raise ValueError('An employee already uses this username or phone number.')
        db.users.insert_one(employee,session=session)
        notification = notice('Manager registered employee: '+doc['full_name'],'hr') if manager_onboarding else notice('Employee activated in your branch: '+doc['full_name'],'manager',str(assigned_manager['_id']))
        persist(current,{'stage':'onboarded','status':'Hired','employee_id':str(employee_id),'assigned_branch':branch,'onboarded_at':stamp.isoformat(),'onboarded_by':actor(user)},entry,notification,session=session)
        return db.hr_recruits.find_one({'_id':doc['_id']},session=session)
    try:
        with db.client.start_session() as session:
            saved=session.with_transaction(commit,read_concern=ReadConcern('snapshot'),write_concern=WriteConcern('majority'))
    except DuplicateKeyError:raise ValueError('An employee already uses this username or candidate record. Refresh before retrying.')
    return jsonify(ok=True,candidate=public(saved,user),employee_id=saved['employee_id'])

@recruitment_flow_bp.post('/api/recruitment/candidates/<candidate_id>/notifications/<notification_id>/read')
def read_notification(candidate_id,notification_id):
    user=identity();doc=find(candidate_id,user)
    n=next((n for n in doc.get('notifications',[]) if n['id']==notification_id),None)
    if not n or n['role']!=user['role'] or (n.get('user_id') and n['user_id']!=str(user['user_id'])):abort(404)
    if str(user['user_id']) in n.get('read_by',[]):return jsonify(ok=True)
    updated=[{**entry,'read_by':list(entry.get('read_by',[]))} for entry in doc['notifications']]
    for entry in updated:
        if entry['id']==notification_id:entry['read_by'].append(str(user['user_id']))
    result=db.hr_recruits.update_one({'_id':doc['_id'],'notifications':doc['notifications']},{'$set':{'notifications':updated},'$inc':{'version':1}})
    if not result.modified_count:return jsonify(ok=False,message='Notifications changed. Refresh and try again.'),409
    return jsonify(ok=True)


@recruitment_flow_bp.get('/api/recruitment/branch-staff')
def branch_staff():
    user=identity();branch=clean(request.args.get('branch',''),'Branch',True,150)
    if user['role']=='manager' and branch!=branch_for(user):abort(403)
    employees=list(db.users.find({**active_query(),'status':{'$in':['Active','active']},'branch':branch},{'name':1,'username':1,'role':1,'start_date':1}).sort('name',1))
    return jsonify(ok=True,employees=[{'id':str(p['_id']),'name':p.get('name') or p.get('username','Staff'),'role':p.get('role',''),'start_date':str(p.get('start_date') or '')} for p in employees])
