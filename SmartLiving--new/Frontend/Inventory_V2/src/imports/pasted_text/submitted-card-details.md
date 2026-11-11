6. ROW ACTIONS
--------------------------------------------------

Each row should support:

- View Details
- Approve / Reject
- Assign Stock
- Mark Packing
- Mark Ready for Delivery
- Assign Driver
- Mark In Transit
- Mark Delivered
- Mark Partial Delivery
- Raise Issue
- Update Custom Status
- Print Fulfillment Slip
- Export Record

--------------------------------------------------
7. SUBMITTED CARD DETAIL DRAWER / PAGE
--------------------------------------------------

When user opens a submitted card, show a detailed drawer or full detail page.

Sections:

A. Customer Information
- Customer name
- Phone
- Address
- Branch
- Agent
- Customer ID

B. Product Card Information
- Card name
- Card price
- Payment completion date
- Amount paid
- Balance remaining = 0
- Submission date

C. Items Required
For each item inside the product card:
- Product name
- SKU
- Required quantity
- Available quantity
- Reserved quantity
- Assigned quantity
- Warehouse/room location
- Serial number if applicable
- Stock readiness

D. Fulfillment Timeline
Show:
Submitted → Approved → Stock Assigned → Packing → In Transit → Delivered

Each timeline event should show:
- User
- Role
- Time
- Notes

E. Delivery Information
- Driver
- Vehicle
- Dispatch date
- Delivery date
- Proof of delivery
- Delivery notes
- Customer confirmation

F. Activity Log
Every action on this submission.

--------------------------------------------------
8. STOCK ASSIGNMENT FLOW
--------------------------------------------------

When inventory staff clicks “Assign Stock”:

Open modal:
- Select branch
- Select warehouse/room
- Show products required
- Search stock location
- Assign quantity
- Assign serial number if required

Rules:
- Cannot assign more than available stock
- Serial-tracked products require serial selection
- Assigned stock moves from Available to Committed/Reserved for this submitted card
- If stock is insufficient, mark as Awaiting Stock or Partial Ready

--------------------------------------------------
9. BULK STATUS UPDATE
--------------------------------------------------

Allow selecting multiple submitted cards and updating status.

Bulk actions:
- Approve selected
- Mark packing
- Mark ready for delivery
- Assign warehouse/room
- Export selected
- Create delivery batch

Rules:
- Bulk update must log user, time, and previous/new status
- Restricted statuses require permission

--------------------------------------------------
10. DELIVERY BATCH
--------------------------------------------------

Allow inventory staff to group submitted cards for delivery.

Create Delivery Batch fields:
- Batch ID
- Branch
- Driver
- Vehicle
- Date
- Submitted cards included
- Total customers
- Total items
- Status

Batch statuses:
- Draft
- Packed
- Dispatched
- In Transit
- Delivered
- Partial
- Failed

--------------------------------------------------
11. EXPORT FEATURES
--------------------------------------------------

Export options:
- Export all submitted cards
- Export filtered results
- Export selected rows
- Export by status
- Export delivery batch
- Export fulfillment report

Formats:
- CSV
- Excel
- PDF

Export should include:
- Customer
- Phone
- Branch
- Agent
- Product card
- Items
- Status
- Assigned stock
- Delivery status
- Last update
- Notes

--------------------------------------------------
12. DASHBOARD CONNECTION
--------------------------------------------------

Dashboard should show:
- Submitted cards today
- Pending submitted cards
- Delivered submitted cards
- Delayed submitted cards
- Average fulfillment time
- Oldest pending submission

Notification center should alert:
- New submitted card
- Submitted card waiting too long
- Stock not assigned
- Delivery delayed
- Issue raised

--------------------------------------------------
13. SETTINGS CONNECTION
--------------------------------------------------

Settings should include:

Submitted Card Status Settings:
- Create custom status
- Choose color
- Choose workflow order
- Set permission required
- Enable/disable status

Approval rules:
- Who can approve submitted cards
- Who can assign stock
- Who can mark delivered
- Who can create delivery batch

--------------------------------------------------
14. ROLE ACCESS CONTROL
--------------------------------------------------

Agent:
- Can submit completed cards
- Can view own submitted cards status only

Branch Manager:
- Can view branch submitted cards
- Can approve/reject if permitted

Inventory/Warehouse User:
- Can view assigned branch/warehouse submitted cards
- Can assign stock
- Can update packing/delivery status

Admin/Executive:
- Full access
- Export
- Override status
- View reports

--------------------------------------------------
15. SYSTEM RULES
--------------------------------------------------

- Agent can only submit when customer card balance = 0
- Submission creates a Submitted Card record
- Inventory team cannot mark delivered unless delivery proof is provided if required
- Every status update must create an activity log
- Every stock assignment must update inventory ledger
- Submitted cards with incomplete stock should show Awaiting Stock
- Delivered status should close the fulfillment workflow
- Rejected submissions must require reason
- Custom status updates must not break the main workflow

--------------------------------------------------
16. UI DESIGN
--------------------------------------------------

Use a modern operations dashboard style.

Design should include:
- Colorful metric cards
- Status badges
- Timeline workflow
- Advanced filters
- Bulk actions bar
- Export dropdown
- Detail drawer
- Stock assignment modal
- Delivery batch modal

The page should feel like a command center for completed product card fulfillment.

--------------------------------------------------
FINAL GOAL
--------------------------------------------------

Create an advanced Submitted Cards page that allows inventory and warehouse teams to manage all agent-submitted completed customer product cards from submission through stock assignment, packing, transit, delivery, and closure, with metrics, status tracking, exports, custom statuses, permissions, and full audit history.