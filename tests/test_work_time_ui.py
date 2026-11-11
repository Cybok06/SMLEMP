import os
from pathlib import Path
from threading import Thread
import pytest
from flask import session, render_template, send_from_directory
from werkzeug.serving import make_server
from test_work_time import work_flow
playwright=pytest.importorskip('playwright.sync_api')

def test_clock_locations_denied_permission_and_hr_approval_browser(work_flow,tmp_path):
    client,db,signin,people,*_=work_flow;app=client.application
    @app.get('/_login/<role>')
    def login(role):session.clear();session.update(role=role,user_id=str(people[role]));return 'ok'
    @app.get('/_dashboard')
    def dashboard():return render_template('work_time_summary.html')
    root=Path(__file__).resolve().parents[1]
    @app.get('/customer-support/app/')
    @app.get('/customer-support/app/<path:filename>')
    def support_app(filename='index.html'):return send_from_directory(root/'Frontend/Customer_Support/dist',filename)
    @app.get('/inventory/app/')
    @app.get('/inventory/app/<path:filename>')
    def inventory_app(filename='index.html'):return send_from_directory(root/'Frontend/Inventory_V2/dist',filename)
    server=make_server('127.0.0.1',0,app,threaded=True);Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}';errors=[]
    try:
        with playwright.sync_playwright() as p:
            channel=os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser=p.chromium.launch(headless=True,channel=channel);context=browser.new_context(permissions=['geolocation'],geolocation={'latitude':5.6037,'longitude':-0.187},viewport={'width':1440,'height':1000});page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/_login/admin');page.goto(base+'/admin/work-time');page.get_by_text('Configure company work hours',exact=True).click();page.get_by_label('Grace period (minutes)',exact=True).fill('5');page.get_by_role('button',name='Save work hours',exact=True).click();playwright.expect(page.locator('.wt-management>.wt-message')).to_have_text('Company work hours saved.')
            page.goto(base+'/_login/agent');page.goto(base+'/log-time');page.get_by_role('button',name='Clock In / Start Work').click();playwright.expect(page.get_by_text('At Work',exact=True).first).to_be_visible();playwright.expect(page.get_by_role('link',name='View current location on Google Maps')).to_have_count(1)
            context.set_geolocation({'latitude':5.61,'longitude':-0.2});page.get_by_role('button',name='Clock Out / End Work').click();playwright.expect(page.get_by_text('Closed for Day',exact=True).first).to_be_visible();playwright.expect(page.get_by_role('link',name='View current location on Google Maps')).to_have_count(2)
            doc=db.attendance_workdays.find_one({'user_id':str(people['agent'])});assert doc['events'][0]['location']['latitude']==5.61 and doc['events'][-1]['location']['latitude']==5.6037
            form=page.locator('.wt-leave-form');form.get_by_label('Leave type',exact=True).select_option('Permission');form.get_by_label('Reason',exact=True).fill('Family appointment');form.get_by_role('button',name='Send request to HR',exact=True).click();playwright.expect(page.locator('.wt-my-leaves')).to_contain_text('Pending');page.screenshot(path=str(tmp_path/'agent-log-time.png'),full_page=True)
            page.goto(base+'/_login/hr');page.goto(base+'/hr/work-time');playwright.expect(page.locator('.wt-pending')).to_contain_text('Family appointment');page.locator('.wt-pending').get_by_role('button',name='Approve',exact=True).click();playwright.expect(page.locator('.wt-pending')).to_contain_text('No requests awaiting review.');page.locator('.wt-late-only').check();playwright.expect(page.locator('.wt-report-table')).to_contain_text('Late by 6 min');page.screenshot(path=str(tmp_path/'hr-work-time.png'),full_page=True)
            page.goto(base+'/_login/agent');page.goto(base+'/log-time');playwright.expect(page.locator('.wt-my-leaves')).to_contain_text('Approved');page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth');page.screenshot(path=str(tmp_path/'log-time-mobile.png'),full_page=True)
            denied=browser.new_context(viewport={'width':1440,'height':1000});denied.add_init_script("Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition:(success,error)=>error({code:1})}})");other=denied.new_page();other.on('pageerror',lambda error:errors.append(str(error)));other.goto(base+'/_login/manager');other.goto(base+'/log-time');other.get_by_role('button',name='Clock In / Start Work').click();playwright.expect(other.get_by_text('At Work',exact=True).first).to_be_visible();assert db.attendance_workdays.find_one({'user_id':str(people['manager'])})['events'][0]['location']['status']=='denied'
            page.set_viewport_size({'width':1440,'height':1000});page.goto(base+'/_login/customer_support')
            page.route('**/api/customer-support/**',lambda route:route.fulfill(json={'ok':True,'profile':{'name':'Support Employee','role':'customer_support','avatar_initials':'SE'}} if route.request.url.endswith('/me') else {'ok':False,'message':'Unrelated API omitted from attendance fixture'}))
            page.goto(base+'/customer-support/app/');playwright.expect(page.get_by_role('region',name='My attendance and leave')).to_contain_text('Workdays reported');page.screenshot(path=str(tmp_path/'react-attendance-debug.png'),full_page=True);page.get_by_role('button',name='Log Time / Leave',exact=True).click();playwright.expect(page.get_by_role('heading',name='Log Time',exact=True)).to_be_visible()
            page.goto(base+'/_login/inventory');page.goto(base+'/inventory/app/');playwright.expect(page.get_by_role('region',name='My attendance and leave')).to_contain_text('Workdays reported');page.get_by_role('button',name='Log Time / Leave',exact=True).click();playwright.expect(page.get_by_role('heading',name='Log Time',exact=True)).to_be_visible()
            assert not errors,errors;browser.close()
    finally:server.shutdown()
