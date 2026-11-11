from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from bson.objectid import ObjectId
from datetime import datetime
from db import db

# Collections
tasks_collection = db.tasks
customers_collection = db.customers
users_collection = db.users  # Needed to find admin/manager who sent task

agent_tasks_bp = Blueprint('agent_tasks', __name__)

@agent_tasks_bp.route('/agent/tasks')
@agent_tasks_bp.route('')
@login_required
def view_tasks():
    if current_user.role != 'agent':
        return "Unauthorized", 403

    agent_id = ObjectId(current_user.id)

    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    query = {'target_type': 'agent', '$or': [{'user_id': agent_id}, {'user_id': 'all'}]}
    total = tasks_collection.count_documents(query)
    pages = max(1, (total + 24) // 25)
    page = min(page, pages)
    tasks = list(tasks_collection.find(query).sort([('timestamp', -1), ('_id', -1)]).skip((page - 1) * 25).limit(25))
    customer_ids = list({task['customer_id'] for task in tasks if task.get('customer_id')})
    sender_ids = list({task.get('admin_id') or task.get('manager_id') for task in tasks if task.get('admin_id') or task.get('manager_id')})
    customers = {str(doc['_id']): doc for doc in customers_collection.find({'_id': {'$in': customer_ids}}, {'name': 1})} if customer_ids else {}
    senders = {str(doc['_id']): doc for doc in users_collection.find({'_id': {'$in': sender_ids}}, {'username': 1})} if sender_ids else {}
    for task in tasks:
        customer = customers.get(str(task.get('customer_id')))
        task['customer_name'] = customer.get('name', 'Unknown') if customer else ('Unknown' if task.get('customer_id') else '?')
        if task.get('admin_id') or task.get('manager_id'):
            prefix = 'Admin' if task.get('admin_id') else 'Manager'
            sender = senders.get(str(task.get('admin_id') or task.get('manager_id')))
            task['sent_by'] = f"{prefix} {sender['username']}" if sender else prefix
        else:
            task['sent_by'] = 'Customer Support' if task.get('created_by_role') == 'customer_support' else 'Unknown'

    return render_template('task.html', tasks=tasks, page=page, pages=pages, total=total)

@agent_tasks_bp.route('/agent/tasks/count')
@agent_tasks_bp.route('/count')
@login_required
def pending_task_count():
    if current_user.role != 'agent':
        return jsonify(count=0), 403
    count = tasks_collection.count_documents({
        'target_type': 'agent',
        'status': {'$in': ['pending', 'in progress']},
        '$or': [{'user_id': ObjectId(current_user.id)}, {'user_id': 'all'}]
    })
    return jsonify(count=count)

@agent_tasks_bp.route('/agent/tasks/<task_id>/status', methods=['POST'])
@agent_tasks_bp.route('/<task_id>/status', methods=['POST'])
@login_required
def update_task_status(task_id):
    if current_user.role != 'agent':
        return "Unauthorized", 403
    try:
        task = tasks_collection.find_one({'_id': ObjectId(task_id)})
    except Exception:
        task = None
    if not task:
        flash("Task not found.", "danger")
        return redirect(url_for('agent_tasks.view_tasks'))
    if task.get('target_type') != 'agent' or (task.get('user_id') != 'all' and str(task.get('user_id')) != current_user.id):
        flash("You are not authorized to update this task.", "danger")
        return redirect(url_for('agent_tasks.view_tasks'))
    status = request.form.get('status', '').strip().lower()
    if status not in {'pending', 'in progress', 'completed'}:
        flash("Invalid task status.", "danger")
        return redirect(url_for('agent_tasks.view_tasks'))
    now = datetime.utcnow()
    updates = {'status': status, 'updated_at': now, 'completed_at': now if status == 'completed' else None}
    tasks_collection.update_one({'_id': task['_id']}, {'$set': updates})
    flash("Task status updated.", "success")
    return redirect(url_for('agent_tasks.view_tasks'))

@agent_tasks_bp.route('/agent/tasks/<task_id>/complete', methods=['POST'])
@agent_tasks_bp.route('/<task_id>/complete', methods=['POST'])
@login_required
def mark_task_complete(task_id):
    if current_user.role != 'agent':
        return "Unauthorized", 403

    task = tasks_collection.find_one({'_id': ObjectId(task_id)})

    if not task:
        flash("Task not found.", "danger")
        return redirect(url_for('agent_tasks.view_tasks'))

    # Ensure the agent is authorized to complete this task
    if task['target_type'] != 'agent':
        flash("This task was not assigned to an agent.", "danger")
        return redirect(url_for('agent_tasks.view_tasks'))

    if task['user_id'] != 'all' and str(task['user_id']) != current_user.id:
        flash("You are not authorized to complete this task.", "danger")
        return redirect(url_for('agent_tasks.view_tasks'))

    # Mark task as completed
    tasks_collection.update_one(
        {'_id': ObjectId(task_id)},
        {'$set': {'status': 'completed', 'completed_at': datetime.utcnow()}}
    )

    flash("Task marked as completed.", "success")
    return redirect(url_for('agent_tasks.view_tasks'))
