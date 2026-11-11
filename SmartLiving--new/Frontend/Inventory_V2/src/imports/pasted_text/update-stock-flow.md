Update the Inventory page by adding a new button and modal flow called “Update Stock”.

Purpose:
Allow authorized users to update stock quantities for products in a selected branch and warehouse/room through a controlled session, then close the session with a summary report.

--------------------------------------------------
1. INVENTORY PAGE UPDATE
--------------------------------------------------

Add a primary action button at the top-right of the Inventory page:

Button:
“Update Stock”

Place near:
- Add Inventory Item
- Export
- Filters

When clicked, open a multi-step modal / wizard.

--------------------------------------------------
2. UPDATE STOCK SESSION FLOW
--------------------------------------------------

The update must happen inside a session.

Session purpose:
Group multiple stock updates together so the system can track who updated what, where, when, and why.

Session steps:

Step 1: Select Location
Step 2: Add Stock Updates
Step 3: Review & Close Session
Step 4: Session Report

--------------------------------------------------
3. STEP 1 — SELECT LOCATION
--------------------------------------------------

User selects:

- Branch
- Warehouse / Room under selected branch

Example:
Branch: Tema Branch
Warehouse/Room: Room A

Rules:
- Warehouse/Room options must depend on selected branch
- User can only see branches/warehouses they have permission to access
- Cannot continue without selecting both branch and warehouse/room

Fields:
- Branch
- Warehouse / Room
- Update reason / session note

Example reasons:
- New stock received
- Manual correction
- Damaged stock removed
- Returned stock added
- Stock transfer adjustment
- Opening balance update

--------------------------------------------------
4. STEP 2 — ADD STOCK UPDATES
--------------------------------------------------

Show a clean product search and update interface.

Fields:
- Search product by name / SKU
- Select product
- Current quantity at selected warehouse/room
- Update type:
  - Add stock
  - Subtract stock
- Quantity
- Optional note

When product is added to the session, show it in an update list/table.

Update list table:
- Product
- SKU
- Current quantity
- Action (+ / -)
- Quantity changed
- New quantity preview
- Unit cost price
- Stock value impact
- Note
- Remove row action

Rules:
- Quantity must be greater than 0
- Subtract stock cannot exceed available quantity unless user has special override permission
- New quantity preview must update instantly
- Every product update must be linked to the selected branch and warehouse/room

--------------------------------------------------
5. PRICING / VALUE CONTROL
--------------------------------------------------

Because cost price is sensitive:

For Warehouse / Branch / Logistics users:
- Hide cost price
- Hide stock value impact

For Finance / Executive / Admin users:
- Show unit cost price
- Show total stock value impact

Stock value impact formula:
Quantity Changed x Cost Price

--------------------------------------------------
6. STEP 3 — REVIEW & CLOSE SESSION
--------------------------------------------------

Before closing session, show a review screen.

Review summary:
- Branch
- Warehouse/Room
- Session reason
- Total products updated
- Total quantity added
- Total quantity subtracted
- Net quantity change
- Total stock value impact
- Updated by
- Date/time

Show update table:
- Product
- Old quantity
- Change
- New quantity
- Note

Buttons:
- Save as Draft
- Close Session
- Cancel

Rules:
- Stock should only be permanently updated when session is closed/confirmed
- Closing session creates inventory ledger entries
- Each product update becomes a ledger movement/adjustment record
- Closed session cannot be edited
- If correction is needed after closing, create a reversal or new update session

--------------------------------------------------
7. STEP 4 — SESSION REPORT
--------------------------------------------------

After closing the session, generate a quick report.

Report title:
“Stock Update Session Report”

Report fields:
- Session ID
- Branch
- Warehouse/Room
- Updated by
- Date/time opened
- Date/time closed
- Session status
- Reason

Summary cards:
- Products updated
- Total quantity added
- Total quantity subtracted
- Net stock change
- Total stock value impact
- Number of high-value items updated

Detailed table:
- Product
- SKU
- Old quantity
- Quantity added/subtracted
- New quantity
- Unit cost price
- Value impact
- Note

Actions:
- Print report
- Export PDF
- Export CSV
- View ledger entries

--------------------------------------------------
8. LEDGER & AUDIT REQUIREMENTS
--------------------------------------------------

Every closed stock update session must create inventory ledger records.

Ledger fields:
- Ledger ID
- Session ID
- Product ID
- SKU
- Branch
- Warehouse/Room
- Old quantity
- Change quantity
- New quantity
- Movement type
- Reason
- Updated by
- Date/time

Movement types:
- Stock Update Add
- Stock Update Subtract
- Manual Correction
- Opening Balance
- Damaged Removal
- Returned Stock Addition

Audit rules:
- All actions must be logged
- User, role, date, time, location, and reason required
- Closed sessions cannot be deleted
- Only reversal records can correct a closed session
- Large stock changes should trigger approval or alert

--------------------------------------------------
9. APPROVAL RULES
--------------------------------------------------

Add optional approval logic for risky changes.

Require approval when:
- Subtracting stock above a configured threshold
- Stock value impact exceeds a configured amount
- User is not Admin/Finance/Manager
- Quantity becomes negative
- Update reason is “Manual Correction”

Approval statuses:
- Draft
- Pending Approval
- Approved
- Closed
- Rejected

--------------------------------------------------
10. DASHBOARD / REPORT CONNECTION
--------------------------------------------------

Update related pages to show this session.

Inventory page:
- Show recent stock update sessions

Inventory detail page:
- Show stock update history for that product

Audit & Accountability:
- Flag suspicious update sessions

Reports & Analytics:
- Add Stock Update Session Report
- Add Manual Adjustment Report

Notification Center:
- Alert managers when large or risky stock update is closed

--------------------------------------------------
11. UI DESIGN
--------------------------------------------------

Design the modal as a modern step-by-step wizard.

Use:
- Step indicator
- Searchable product dropdown
- Dynamic quantity preview
- Colored + / - badges
- Summary cards
- Clean review screen

Make it mobile-friendly enough for warehouse staff using tablets.

Use professional colors:
- Green for stock added
- Red/orange for stock subtracted
- Blue for neutral session info

--------------------------------------------------
FINAL GOAL
--------------------------------------------------

Create a controlled, auditable “Update Stock” session system that allows users to update warehouse products quickly while preserving accuracy, accountability, permission control, ledger history, and report visibility.