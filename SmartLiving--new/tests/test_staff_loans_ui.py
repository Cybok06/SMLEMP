import os
from pathlib import Path
from threading import Thread
import pytest
from flask import session, render_template
from werkzeug.serving import make_server
from test_staff_loans import loan_flow, payload
playwright=pytest.importorskip("playwright.sync_api")

def test_management_split_payment_employee_dashboard_browser(loan_flow,tmp_path):
    client,db,signin,agent,manager,admin=loan_flow;app=client.application
    app.context_processor(lambda: {'identity': {'user_id':session.get('user_id'), 'role':session.get('role')}})
    app.url_build_error_handlers.append(lambda error,endpoint,values:'/_unused/'+endpoint)
    @app.get('/_login/<role>')
    def login(role):
        session.clear();session.update(role=role,user_id=str(agent if role=='agent' else manager if role=='manager' else admin));return 'ok'
    @app.get('/_dashboard')
    def dashboard():return render_template('staff_loans_dashboard.html')
    @app.get('/_profile-loans')
    def profile():return render_template('staff_loans_content.html',loan_management=True,loan_employee_id=str(agent))
    server=make_server('127.0.0.1',0,app,threaded=True);Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}';errors=[]
    try:
        with playwright.sync_playwright() as p:
            channel=os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser=p.chromium.launch(headless=True,channel=channel);page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/_login/hr');page.goto(base+'/hr/staff-loans');page.get_by_text('Record staff debt',exact=True).click()
            page.screenshot(path=str(tmp_path/'debug-initial.png'),full_page=True);form=page.locator('.sl-loan-form');form.locator('select[name=employee_id]').select_option(str(agent));form.get_by_label('Total cost (GHS)',exact=True).fill('1200.50');form.get_by_label('Company contribution (GHS)',exact=True).fill('200.25');form.get_by_label('Reason',exact=True).fill('Company paid training');playwright.expect(page.locator('.sl-share')).to_have_text('Employee share: GHS 1,000.25');form.get_by_role('button',name='Record debt',exact=True).click()
            playwright.expect(page.locator('.sl-record')).to_have_count(1);playwright.expect(page.locator('.sl-record')).to_contain_text('GHS 1,000.25')
            page.get_by_text('Record repayment / deduction',exact=True).click();payment=page.locator('.sl-payment-form');payment.get_by_label('Amount (GHS)',exact=True).fill('100.25');payment.get_by_label('Type',exact=True).select_option('Payroll deduction');payment.get_by_label('Receipt / payroll reference',exact=True).fill('Payroll October');payment.get_by_role('button',name='Record payment',exact=True).click();playwright.expect(page.locator('.sl-record')).to_contain_text('GHS 900.00');page.get_by_text('Repayment and deduction history (1)',exact=True).click();playwright.expect(page.get_by_role('cell',name='Payroll deduction',exact=True)).to_be_visible();page.screenshot(path=str(tmp_path/'hr-staff-loan.png'),full_page=True)
            page.goto(base+'/_profile-loans');playwright.expect(page.locator('.sl-record')).to_have_count(1);playwright.expect(page.locator('select[name="employee_id"]')).to_be_disabled()
            for role in ['admin','executive']:
                page.goto(base+'/_login/'+role);page.goto(base+'/'+role+'/staff-loans');playwright.expect(page.locator('.sl-record')).to_have_count(1)
            page.goto(base+'/_login/agent');page.goto(base+'/_dashboard');playwright.expect(page.locator('.sl-dashboard-values')).to_contain_text('GHS 900.00');page.get_by_role('link',name='View loans, repayments and payment due').click();playwright.expect(page.locator('.sl-record')).to_have_count(1);assert page.locator('.sl-loan-form').count()==0 and page.locator('.sl-payment-form').count()==0;page.screenshot(path=str(tmp_path/'agent-staff-loans.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844});page.reload();playwright.expect(page.locator('.sl-record')).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.goto(base+'/_login/manager');page.goto(base+'/staff-loans');playwright.expect(page.get_by_text('No staff loans recorded.',exact=True)).to_be_visible()
            page.goto(base+'/_login/hr');page.goto(base+'/hr/staff-loans');page.get_by_text('Record staff debt',exact=True).click();form=page.locator('.sl-loan-form');form.get_by_label('Staff member',exact=True).select_option('manual-new');form.get_by_label('Full name',exact=True).fill('Manual Person');form.get_by_label('Phone',exact=True).fill('0241234567');form.get_by_label('Staff reference / employee number',exact=True).fill('MAN-UI');form.get_by_label('Total cost (GHS)',exact=True).fill('300');form.get_by_label('Reason',exact=True).fill('Salary advance');form.get_by_role('button',name='Record debt',exact=True).click();playwright.expect(page.locator('.sl-record')).to_have_count(2)
            assert form.locator('select[name=employee_id] option').filter(has_text='Manual Person').count()==1
            form.get_by_label('Staff member',exact=True).select_option(str(manager));form.get_by_label('Total cost (GHS)',exact=True).fill('200');form.get_by_label('Reason',exact=True).fill('Manager advance');form.get_by_role('button',name='Record debt',exact=True).click();playwright.expect(page.locator('.sl-record')).to_have_count(3)
            page.goto(base+'/_login/manager');page.goto(base+'/_dashboard');playwright.expect(page.locator('.sl-dashboard-values')).to_contain_text('GHS 200.00');page.goto(base+'/staff-loans');playwright.expect(page.locator('.sl-record')).to_have_count(1);playwright.expect(page.locator('.sl-record')).to_contain_text('Manager advance')
            assert not errors,errors;browser.close()
    finally:server.shutdown()
