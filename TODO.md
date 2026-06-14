# TODO
- Under income page 
    - While adding row to salary i got this error in terminal  File "C:\Users\ylnha\Projects\family-finance-app\server.py", line 352, in do_PUT
    with open(tmp, "w", encoding="utf-8") as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 13] Permission denied: 'C:\\Users\\ylnha\\Projects\\family-finance-app\\data\\finances.json.tmp'
----------------------------------------
- Under portfolio page have charts one for each person and a seprate chart for gold by person and at family level single combined visualization which include gold 
- In the dashboard have two visualizations one is monthly investment allocation pie chart by percentage across multiple groups and then portfolio allocation across classes which you already have, you can keep it that way.
- and for ploy loads which hasve been already added give option add record part payment and either ask user to adjust tenure or ami amount as you know you can calculate the amount pre payed peidng intrest and what ever you are doing for fresh or midway load you can do the same, once user records prepayment code logic will update all numbers accordingly.
- In main dashbaord have assets value and also show asset value by type if asset like liquid, equity , real estate and then show liability value an dthen show networth if possible have a visualization there 
- goal value update based on the todays price use gemini models search with grounding to fetch data if theere is an error or issue there simply use last calcua;ted value give user a button when pressed use the same process and if found value update it , under physical gold section let user add the proce at which they have boaught if given calcualte the investment gain , if not ignore and add this to investment performance and also have visualization for that in the dashbaord on how well it performed same visualizations keep in portfolio page as well

All items from the previous list were implemented on 2026-06-14:

- [x] Under Income Page
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


