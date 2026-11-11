from pathlib import Path
from threading import Thread

import pytest
from flask import redirect, render_template, session, Response, send_from_directory
from bson import ObjectId
from werkzeug.serving import make_server

from test_undelivered_products import missing_flow, ROOT

playwright = pytest.importorskip('playwright.sync_api')


def test_agent_records_missing_products_and_manager_updates_shared_status(missing_flow):
    app, _, database, _, manager, _, agent, _, customer, inventory, _ = missing_flow

    officer = ObjectId()
    database.users.insert_one({'_id': officer, 'role': 'inventory', 'name': 'Inventory Officer'})

    @app.get('/inventory/app/')
    @app.get('/inventory/app/<path:filename>')
    def inventory_app(filename='index.html'):
        return send_from_directory(ROOT / 'Frontend/Inventory_V2/dist', filename)

    @app.get('/api/inventory/session')
    def inventory_session():
        return {'ok': True, 'user': {'id': str(officer), 'name': 'Inventory Officer', 'role': 'inventory'},
                'effective_role_id': 'inventory-user', 'roles': [{'id': 'inventory-user', 'name': 'Inventory User',
                'permissions': {'undelivered-items': {'visible': True, 'view': True, 'edit': True}}}]}

    @app.get('/api/inventory/submitted-cards/counts')
    def inventory_counts():
        return {'ok': True, 'counts': {}}

    @app.get('/test/signin/<role>')
    def signin(role):
        key = officer if role == 'inventory' else manager if role == 'manager' else agent
        session.clear();session.update(role=role,user_id=str(key),**{role+'_id':str(key)})
        return redirect('/inventory/app/' if role == 'inventory' else '/test/customer' if role == 'agent' else '/undelivered-products')

    @app.get('/test/customer')
    def profile():
        html = render_template('partials/customer_tabs/products.html', customer_id=str(customer), purchases=[{
            'product': {'name': 'Kitchen package', 'status': 'active', 'total': 100, 'quantity': 1},
            'amount_left': 100, 'amount_paid': 0, 'components_catalog': [], 'progress': 0}])
        source = (ROOT/'templates/customer_profile.html').read_text(encoding='utf-8')
        script = source[source.index('  function addManualRow('):source.index('  function bindProductImagePreview(')]
        # Exercise the production partial and form handlers offline. Bootstrap's
        # modal adapter is replaced only in this fixture (no CDN dependency).
        adapter = """
        window.bootstrap={Modal:class {constructor(element){this.element=element} static getInstance(){return null} hide(){this.element.style.display='none';this.element.dispatchEvent(new Event('hidden.bs.modal'))}}};
        document.addEventListener('click',event=>{const button=event.target.closest('[data-bs-target]');if(button){const modal=document.querySelector(button.dataset.bsTarget);if(modal){modal.style.display='block';modal.removeAttribute('aria-hidden');modal.dispatchEvent(new Event('show.bs.modal'));}}});
        """
        return '<style>.modal{display:none;border:1px solid #ddd;padding:20px;background:white}.d-none{display:none!important}.list-group-item{display:flex;gap:8px}input,select,button{margin:5px;padding:8px}</style>'+html+'<script>'+adapter+script+'\nbindUndeliveredForms(document);</script>'

    server=make_server('127.0.0.1',0,app,threaded=True)
    Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            browser=p.chromium.launch(headless=True,channel='msedge')
            agent_context=browser.new_context(viewport={'width':1440,'height':1000})
            inventory_context=browser.new_context(viewport={'width':1440,'height':1000})
            manager_context=browser.new_context(viewport={'width':1440,'height':1000})
            for context in (agent_context,manager_context,inventory_context):context.route('https://**',lambda route:route.abort())
            agent_page=agent_context.new_page();manager_page=manager_context.new_page()
            for page in (agent_page,manager_page):page.set_default_timeout(15000)
            agent_page.goto(base+'/test/signin/agent')
            agent_page.get_by_role('button',name='Undelivered',exact=True).click()
            modal=agent_page.locator('#undeliveredModal0')
            playwright.expect(modal).to_be_visible()
            modal.get_by_label('Inventory product',exact=True).select_option(str(inventory))
            modal.get_by_role('button',name='Add product',exact=True).click()
            modal.get_by_label('Missing quantity',exact=True).fill('2')
            modal.get_by_role('button',name='Add manual item',exact=False).click()
            modal.locator('.manual-name').fill('Serving tray')
            with agent_page.expect_response(lambda response:'/undelivered/0' in response.url and response.request.method=='POST') as saved:
                modal.get_by_role('button',name='Save',exact=True).click()
            assert saved.value.status==201
            agent_page.goto(base+'/undelivered-products')
            playwright.expect(agent_page.locator('#rowsBody')).to_contain_text('Kitchen package')
            playwright.expect(agent_page.locator('#agentMissingProductsBadge')).to_have_text('1')
            agent_page.get_by_role('button',name='View products (2)',exact=True).click()
            details=agent_page.locator('#undeliveredDetails')
            playwright.expect(details).to_be_visible()
            playwright.expect(details).to_contain_text('Rice cooker')
            playwright.expect(details).to_contain_text('Serving tray')
            details.get_by_role('button',name='Close',exact=True).click()
            manager_page.goto(base+'/test/signin/manager')
            playwright.expect(manager_page.locator('#undeliveredBadge')).to_have_text('1')
            inventory_page=inventory_context.new_page()
            inventory_page.goto(base+'/test/signin/inventory')
            playwright.expect(inventory_page.get_by_role('heading',name='Undelivered Items',exact=True)).to_be_visible()
            playwright.expect(inventory_page.get_by_text('No forwarded items match these filters.')).to_be_visible()
            with manager_page.expect_response(lambda response:response.url.endswith('/send-to-inventory')) as sent:
                manager_page.get_by_role('button',name='Send to Inventory',exact=True).click()
            assert sent.value.status == 200
            playwright.expect(manager_page.get_by_role('button',name='Send to Inventory',exact=True)).to_have_count(0)
            inventory_page.get_by_role('button',name='Refresh',exact=True).click()
            playwright.expect(inventory_page.locator('tbody').get_by_role('cell').filter(has_text='Ama Customer')).to_be_visible()
            inventory_page.get_by_role('button',name='Update with reason',exact=True).click()
            dialog=inventory_page.get_by_role('dialog')
            dialog.get_by_label('Reason / inventory update').fill('Rice cooker awaiting supplier stock')
            dialog.get_by_label('Status',exact=True).select_option('preparing')
            with inventory_page.expect_response(lambda response:response.url.endswith('/inventory-update')) as reason_saved:
                dialog.get_by_role('button',name='Save update',exact=True).click()
            assert reason_saved.value.status == 200
            playwright.expect(inventory_page.get_by_role('status')).to_contain_text('Inventory reason and status saved')
            manager_page.reload()
            playwright.expect(manager_page.locator('#rowsBody')).to_contain_text('Rice cooker awaiting supplier stock')
            agent_page.reload()
            playwright.expect(agent_page.locator('#rowsBody')).to_contain_text('Rice cooker awaiting supplier stock')
            snapshot=ROOT / '.test-tools/inventory-undelivered-items.png'
            snapshot.parent.mkdir(exist_ok=True)
            inventory_page.screenshot(path=str(snapshot),full_page=True)
            for status,label in [('products_in','Products In'),('delivered','Delivered')]:
                manager_page.locator('[data-status-missing]').select_option(status)
                with manager_page.expect_response(lambda response: response.url.endswith('/status') and response.request.method=='POST') as updated:
                    manager_page.locator('[data-save-missing]').click()
                assert updated.value.status == 200
                playwright.expect(manager_page.locator('#undeliveredFeedback')).to_contain_text('Status saved')
                agent_page.reload()
                if status!='delivered':
                    playwright.expect(agent_page.locator('#rowsBody .badge')).to_have_text(label)
                    playwright.expect(agent_page.locator('#agentMissingProductsBadge')).to_have_text('1')
                    if status == 'products_in':
                        snapshot = ROOT / '.test-tools/undelivered-products.png'
                        snapshot.parent.mkdir(exist_ok=True)
                        agent_page.screenshot(path=str(snapshot), full_page=True)
                else:
                    playwright.expect(agent_page.locator('#rowsBody')).to_contain_text('No records')
                    playwright.expect(agent_page.locator('#agentMissingProductsBadge')).to_have_text('0')
                    agent_page.locator('#statusSelect').select_option('delivered')
                    agent_page.get_by_role('button',name='Apply',exact=True).click()
                    playwright.expect(agent_page.locator('#rowsBody .badge')).to_have_text('Delivered')
            assert database.undelivered_items.count_documents({'status':'delivered'})==1
            browser.close()
    finally:server.shutdown()
