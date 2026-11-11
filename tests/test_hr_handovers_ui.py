from pathlib import Path
from threading import Thread
from flask import session,render_template
from jinja2 import ChoiceLoader,DictLoader
from werkzeug.serving import make_server
from test_hr_handovers import handover_flow
from test_customer_support_targets_ui import playwright
import os

def test_hr_snapshot_receiver_acknowledgement_and_confirmation_browser(handover_flow,tmp_path):
    client,db,login,source,target,other,hr,manager,customer,exit_id,_=handover_flow
    app=client.application
    app.jinja_loader=ChoiceLoader([DictLoader({'app_footer.html':'', 'hr_pages/hr_shell.html':'<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head><body>{% block hr_content %}{% endblock %}<script>window.showToast=(type,message)=>{document.getElementById("_toast").textContent=message;};</script><p id="_toast"></p></body></html>'}),app.jinja_loader])
    @app.get('/_test/login/<role>')
    def signin(role):
        id=target if role=='agent' else hr
        session.clear();session.update(role=role,user_id=str(id));session[role+'_id']=str(id);return 'ok'
    @app.get('/_test/account-history')
    def account_history():return render_template('agent_profile.html',agent=db.users.find_one({'_id':target}),login_logs=[],login_stats={})
    server=make_server('127.0.0.1',0,app,threaded=True);thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}';errors=[]
    try:
        with playwright.sync_playwright() as p:
            channel=os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser=p.chromium.launch(headless=True,channel=channel);page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/_test/login/hr');page.goto(base+'/hr/exits')
            page.locator('#exBody tr').first.click();page.locator('[data-tab="transfers"]').click()
            history=page.locator('#exitHandoverHistory');history.get_by_text('Capture handover before account transfer',exact=True).click();history.get_by_role('button',name='Review current accounts').click()
            history.get_by_label('Select Customer A',exact=True).check();history.get_by_label('Receiving salesperson',exact=False).select_option(str(target));history.get_by_role('button',name='Capture permanent handover',exact=True).click()
            playwright.expect(history.get_by_text('Awaiting receiver acknowledgement',exact=True)).to_be_visible()
            history.get_by_text('Review frozen account figures and acknowledgements',exact=True).click();playwright.expect(history.get_by_text('GHS 650.00',exact=True).first).to_be_visible();page.screenshot(path=str(tmp_path/'hr-handover-snapshot.png'),full_page=True)
            frozen=db.hr_customer_handovers.find_one({})['snapshot']
            page.goto(base+'/_test/login/agent');page.goto(base+'/_test/account-history');page.get_by_text('Review frozen account figures and acknowledgements',exact=True).click();page.get_by_role('checkbox').check();page.get_by_label('Acknowledgement notes',exact=True).fill('Reviewed debts and delivery obligations');page.get_by_role('button',name='Acknowledge handover',exact=True).click();playwright.expect(page.get_by_text('Awaiting HR confirmation',exact=True)).to_be_visible();page.get_by_text('Review frozen account figures and acknowledgements',exact=True).click();page.screenshot(path=str(tmp_path/'receiving-account-history.png'),full_page=True)
            db.customers.update_one({'_id':customer},{'$set':{'agent_id':str(target),'purchases.0.product.total':0}})
            db.complaints.update_many({},{'$set':{'status':'Resolved'}})
            page.goto(base+'/_test/login/hr');page.goto(base+'/hr/exits');page.locator('#exBody tr').first.click();page.locator('[data-tab="transfers"]').click();history=page.locator('#exitHandoverHistory');history.get_by_role('checkbox').check();history.get_by_label('HR confirmation notes',exact=True).fill('Confirmed account transferred');history.get_by_role('button',name='Confirm handover as HR',exact=True).click();playwright.expect(history.get_by_text('HR confirmed',exact=True)).to_be_visible()
            history.get_by_text('Review frozen account figures and acknowledgements',exact=True).click();page.screenshot(path=str(tmp_path/'hr-confirmed-handover.png'),full_page=True)
            assert db.hr_customer_handovers.find_one({})['snapshot']==frozen
            assert not errors,errors
            browser.close()
    finally:server.shutdown();thread.join(timeout=5)
