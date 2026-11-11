from threading import Thread
from pathlib import Path
import os
from flask import session, render_template, jsonify, send_from_directory
from werkzeug.serving import make_server
from test_customer_support_targets import flow
from test_delivery_followups import delivery_flow
from test_repeat_purchases import opportunity
from test_customer_support_targets_ui import playwright, DIST

def test_agent_closure_and_support_tracking_browser(opportunity,tmp_path):
    client,db,login,agent,other,customer,id,due=opportunity
    app=client.application
    app._got_first_request=False  # Fixture seeded the opportunity through HTTP before browser routes.
    @app.get('/_test/login/<role>')
    def signin(role):
        session.update(role=role,user_id=str(agent) if role=='agent' else str(db.users.find_one({'role':'customer_support'})['_id']))
        return 'ok'
    @app.get('/_test/complaints')
    def complaints(): return render_template('agent_repeat_purchases.html')
    @app.get('/customer-support/app/')
    def spa(): return send_from_directory(DIST,'index.html')
    @app.get('/customer-support/app/<path:filename>')
    def assets(filename): return send_from_directory(DIST,filename)
    @app.get('/api/customer-support/me')
    def identity(): return jsonify(ok=True,profile={'name':'Officer','role':'customer_support','avatar_initials':'OF','branch':'HQ'})
    server=make_server('127.0.0.1',0,app,threaded=True);thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    errors=[]
    try:
        with playwright.sync_playwright() as p:
            channel=os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser=p.chromium.launch(headless=True,channel=channel);page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/_test/login/agent');page.goto(base+'/_test/complaints');page.get_by_role('button',name='View opportunity').click()
            dialog=page.get_by_role('dialog');dialog.get_by_label('Opportunity status',exact=True).select_option('In Progress');dialog.get_by_label('Follow-up notes').fill('Discussed budget and model');dialog.get_by_role('button',name='Save follow-up').click();playwright.expect(dialog).not_to_be_visible()
            page.get_by_role('button',name='View opportunity').click();dialog.get_by_label('Opportunity status',exact=True).select_option('Closed');dialog.get_by_label('Closure reason').fill('Customer postponed purchase');dialog.get_by_label('Follow-up notes').fill('Customer asked to close this opportunity');dialog.get_by_role('button',name='Save follow-up').click();playwright.expect(dialog).not_to_be_visible()
            page.locator('#rp-filter-status').select_option('Closed');page.get_by_role('button',name='Search / refresh').click();playwright.expect(page.get_by_role('cell',name='Gas cooker',exact=True)).to_be_visible()
            page.goto(base+'/_test/login/customer_support');page.goto(base+'/customer-support/app/#repeatpurchases');page.get_by_label('Opportunity status').select_option('Closed');page.get_by_role('button',name='View opportunity').click();dialog=page.get_by_role('dialog');playwright.expect(dialog.get_by_text('Closure reason: Customer postponed purchase',exact=True)).to_be_visible();playwright.expect(dialog.get_by_text('Discussed budget and model',exact=True)).to_be_visible();page.screenshot(path=str(tmp_path/'repeat-support-tracking.png'),full_page=True)
            assert not errors,errors
            browser.close()
    finally:server.shutdown();thread.join(timeout=5)
