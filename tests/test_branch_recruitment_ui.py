import os
from pathlib import Path
from threading import Thread
import pytest
from flask import session, render_template
from werkzeug.serving import make_server
from test_branch_recruitment import recruit_flow, create, candidate
playwright=pytest.importorskip('playwright.sync_api')

@pytest.mark.parametrize('width', [1440, 390])
def test_hr_application_cards_review_cv_and_dashboard(recruit_flow, tmp_path, monkeypatch, width):
    client, database, signin, ids, flow, recruits = recruit_flow
    app = client.application
    @app.get('/_hr_login')
    def hr_login():
        session.clear()
        session.update(role='hr', user_id=str(ids['hr']), name='HR reviewer')
        return 'ok'
    @app.get('/_recruit_dashboard')
    def dashboard():
        return render_template('recruitment_overview.html')
    @app.get('/_cv.svg')
    def cv():
        return '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/><text x="40" y="70" font-size="30">Candidate One CV</text><text x="40" y="130">Sales experience and qualifications</text></svg>', 200, {'Content-Type': 'image/svg+xml'}
    create(recruit_flow)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(recruits, '_upload_cv_to_cloudflare', lambda file: (base + '/_cv.svg', 'test-cv', None))
    errors = []
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel='msedge')
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/_hr_login')
            page.goto(base + '/hr/recruits')
            card = page.get_by_role('article', name='Application from Candidate One', exact=True)
            playwright.expect(card).to_be_visible()
            assert page.locator('.rc-candidates table').count() == 0
            assert page.get_by_text('Active staff by branch', exact=True).count() == 0
            assert page.get_by_text('Recruitment notifications', exact=True).count() == 0
            card.get_by_role('button', name='Review Candidate One').click()
            detail = page.locator('.rc-detail')
            playwright.expect(detail).to_contain_text('No CV uploaded for this application.')
            review = detail.locator('[data-action=review_status]')
            review.get_by_label('Application status', exact=True).select_option('Shortlisted')
            review.get_by_label('Review notes').fill('Reviewed application and requested an interview.')
            review.get_by_role('button', name='Update application status').click()
            playwright.expect(detail.locator('.rc-heading .rc-review-status')).to_have_text('Shortlisted')
            page.reload()
            playwright.expect(card.locator('.rc-review-status')).to_have_text('Shortlisted')
            card.get_by_role('button', name='Review Candidate One').click()
            detail.get_by_label('Upload candidate CV').set_input_files({'name': 'cv.png', 'mimeType': 'image/png', 'buffer': b'test image'})
            detail.get_by_role('button', name='Save CV', exact=True).click()
            playwright.expect(detail.locator('.rc-cv-preview')).to_have_attribute('src', base + '/_cv.svg')
            playwright.expect(card.get_by_role('link', name='View CV')).to_have_attribute('href', base + '/_cv.svg')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(tmp_path / f'hr-recruitment-review-{width}.png'), full_page=True)
            reject = detail.locator('[data-action=reject]')
            reject.get_by_label('Decision notes').fill('Application does not meet the role requirements.')
            reject.get_by_role('button', name='Reject candidate as HR').click()
            playwright.expect(card.locator('.rc-review-status')).to_have_text('Rejected')
            assert database.hr_recruits.find_one({})['hr_decision']['decided_by']['user_id'] == str(ids['hr'])
            created = page.request.post(base + '/api/recruitment/candidates', data=candidate(full_name='Approved Candidate', phone='0241234500', branch='Accra'))
            assert created.status == 201
            page.reload()
            page.get_by_role('button', name='Review Approved Candidate', exact=True).click()
            approve = detail.locator('[data-action=approve]')
            playwright.expect(approve).to_be_visible()
            approve.get_by_label('Decision notes').fill('HR reviewed the CV and approved employment.')
            approve.get_by_role('button', name='Approve recruitment as HR').click()
            onboard = detail.locator('[data-action=onboard]')
            playwright.expect(onboard).to_be_visible()
            onboard.get_by_label('Assigned branch', exact=True).select_option('Accra')
            onboard.get_by_label('Employee role').select_option('agent')
            onboard.get_by_label('Assigned manager').select_option(str(ids['manager']))
            onboard.get_by_label('Username', exact=True).fill('hr_approved_candidate')
            onboard.get_by_label('Initial password').fill('InitialStrong123')
            onboard.get_by_label('Complete onboarding and activate this employee now').check()
            onboard.get_by_role('button', name='Complete onboarding and activate employee').click()
            playwright.expect(detail).to_contain_text('Employee activated:')
            employee = database.users.find_one({'username': 'hr_approved_candidate'})
            assert employee and employee['name'] == 'Approved Candidate' and employee['is_active'] is True
            page.goto(base + '/_recruit_dashboard')
            playwright.expect(page.locator('.rc-counter[data-stage=rejected] strong')).to_have_text('1')
            assert page.locator('.rc-overview-branches,.rc-notification-link').count() == 0
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()


def test_branch_interview_hr_approval_onboarding_and_dashboard_browser(recruit_flow,tmp_path):
    client,db,signin,ids,*_=recruit_flow;app=client.application
    @app.get('/_login/<key>')
    def login(key):
        person=db.users.find_one({'_id':ids[key]});session.clear();session.update(role=person['role'],user_id=str(person['_id']),name=person['name']);return 'ok'
    @app.get('/_dashboard')
    def dashboard():return render_template('recruitment_overview.html')
    server=make_server('127.0.0.1',0,app,threaded=True);Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}';errors=[]
    try:
        with playwright.sync_playwright() as p:
            channel=os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser=p.chromium.launch(headless=True,channel=channel);page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/_login/manager');page.goto(base+'/manager/recruitment/register');form=page.locator('.rc-register-form');form.get_by_label('Candidate full name',exact=True).fill('Candidate One');form.get_by_label('Phone',exact=True).fill('0241234567');form.get_by_label('Position applied for',exact=True).fill('Sales Agent');form.get_by_role('button',name='Save candidate',exact=True).click()
            detail=page.locator('.rc-detail');playwright.expect(detail.get_by_role('heading',name='Candidate One',exact=True)).to_be_visible();interview=detail.locator('[data-action=branch_interview]');interview.get_by_label('Candidate attended this interview',exact=True).check();interview.get_by_label('Interview notes',exact=True).fill('Branch manager assessed communication and availability');interview.get_by_role('button',name='Record branch interview',exact=True).click();playwright.expect(detail.locator('[data-action=submit_hr]')).to_have_count(1);submit=detail.locator('[data-action=submit_hr]');submit.get_by_label('Responsible HR staff',exact=True).select_option(str(ids['hr']));submit.get_by_role('button',name='Refer to main HR (optional)',exact=True).click();playwright.expect(detail).to_contain_text('Awaiting HR interview');assert detail.locator('[data-action=approve]').count()==0;page.screenshot(path=str(tmp_path/'manager-candidate-submitted.png'),full_page=True)
            page.goto(base+'/_login/hr');page.goto(base+'/_dashboard');playwright.expect(page.locator('.rc-counter[data-stage=awaiting_hr_interview] strong')).to_have_text('1');page.locator('.rc-counter[data-stage=awaiting_hr_interview]').click();playwright.expect(page.locator('.rc-candidates')).to_contain_text('Candidate One');assert page.locator('.rc-notification').count()==0;page.locator('.rc-view').first.click();detail=page.locator('.rc-detail');hr_interview=detail.locator('[data-action=hr_interview]');hr_interview.get_by_label('Candidate attended this interview',exact=True).check();hr_interview.get_by_label('Interview notes',exact=True).fill('Attended second interview at HR and recommended');hr_interview.get_by_role('button',name='Record HR interview',exact=True).click();playwright.expect(detail.locator('[data-action=approve]')).to_be_visible();approve=detail.locator('[data-action=approve]');approve.get_by_label('Decision notes',exact=True).fill('HR final recruitment approval');approve.get_by_role('button',name='Approve recruitment as HR',exact=True).click();playwright.expect(detail.locator('[data-action=onboard]')).to_be_visible()
            onboard=detail.locator('[data-action=onboard]');onboard.get_by_label('Assigned branch',exact=True).select_option('Accra');onboard.get_by_label('Assigned manager',exact=True).select_option(str(ids['manager']));onboard.get_by_label('Username',exact=True).fill('new_agent');onboard.get_by_label('Initial password',exact=True).fill('InitialStrong123');onboard.get_by_label('Complete onboarding and activate this employee now',exact=True).check();onboard.get_by_role('button',name='Complete onboarding and activate employee',exact=True).click();playwright.expect(detail).to_contain_text('Employee activated:');page.locator('.rc-counter[data-stage=onboarded]').click();playwright.expect(page.locator('.rc-candidates')).to_contain_text('Candidate One');assert page.locator('.rc-branch-staff').count()==0;page.screenshot(path=str(tmp_path/'hr-recruitment-onboarded.png'),full_page=True)
            for key in ['admin','executive']:
                page.goto(base+'/_login/'+key);page.goto(base+'/'+key+'/recruitment?stage=onboarded');playwright.expect(page.locator('.rc-candidates')).to_contain_text('Candidate One');page.locator('.rc-view').click();playwright.expect(page.locator('.rc-detail')).to_contain_text('Final HR decision');assert page.locator('.rc-action-form').count()==0
            page.goto(base+'/_login/manager');page.goto(base+'/manager/recruitment');playwright.expect(page.locator('.rc-counter[data-stage=onboarded] strong')).to_have_text('1');page.set_viewport_size({'width':390,'height':844});page.reload();playwright.expect(page.locator('.rc-counter[data-stage=onboarded] strong')).to_have_text('1');assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth');page.screenshot(path=str(tmp_path/'branch-recruitment-mobile.png'),full_page=True)
            assert not errors,errors;browser.close()
    finally:server.shutdown()
