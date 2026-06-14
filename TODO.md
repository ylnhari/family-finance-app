# TODO
- Under income page 
    - add feature fill the values based on payslip, compensation statement or offer letter, either use GenAI model API's like GEMINI or make Subscription models edit data file , and use local model , discuss with user the best method and impliment it criteria is fast efficiient simple and free.
    - Add a Variable Column as a % of CTC , User can add Eligible vs Earned percentage , earned is what we actually got eligible is what we supposed to get, for salary cacluation use earned if provided else eligible. Show variable in yearly ctc and also add a one time bonus which is just pure number show it in yearly ctc , in total at top we should see monthly ctc , yearly ctc and bonus section ( which is vairable plus one time) for in hand salary do not use bonus or one time it is just gross minus deductions. 
    - While adding salary give option to add eitehr monthly values or yearly values , show both values by deriving one from other. also add year  filter so that a person can  have his previous years added. if any person has salary added for multiple years show salary growth in a beautiful visualization.

All items from the previous list were implemented on 2026-06-12:

- [x] Bank name filter on Cards page; cards without a bank are grouped under "No Bank / Other"
- [x] Backups explained & extended — automatic daily backup (first save of each day, last 14 kept) PLUS manual "Backup now" button, backup list, restore and delete in Settings
- [x] Monthly CTC and Gross Income now visible at the top of each earner (was white text on white tiles)
- [x] Salary structure is now relational: components are entered once, each marked "Gross (counts in CTC)" or "CTC only"; CTC = Gross + CTC-only, In-Hand = Gross − Deductions, all auto-computed with validation (no text/negative amounts). Existing in-hand amounts were preserved via a derived "Other deductions" row you can split into real deductions later
- [x] Expenses: per-section predefined categories and predefined locations as dropdowns, with "Add new (save for reuse)" and "Custom (this entry only)" options; each section can track location, person, or nothing
- [x] Monthly investments: dropdowns per column (instrument, person, deducted-from) with predefined + custom support
- [x] Dropdowns everywhere (banks, card types, networks, asset names/classes, goal types, lenders, persons, locations…), prefilled from your existing data
- [x] Loans already update from start date automatically; "EMIs Paid" and an "as of today" note now make this explicit
