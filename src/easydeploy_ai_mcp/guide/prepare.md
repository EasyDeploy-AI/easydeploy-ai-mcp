# Prepare the data

## 1. Frame the problem first

Before touching the data, agree with the user on:

- **The decision** the model supports, and who acts on it.
- **The target**: what exactly is predicted, and for what unit (one customer, order or invoice).
- **The prediction moment**: when the prediction is made. Only information available at that moment may be a feature.
- **The cost of errors**: what a false positive costs versus a false negative; the threshold comes from this.

Settle the decision first. Then write the target as one sentence and confirm it, for example: "`churned` is 1 if the customer had no paid activity in the 90 days after the snapshot date, otherwise 0." Leave out rows whose outcome is not known yet (open deals, active trials) rather than counting them as 0.

## 2. Get the raw data where you can work on it

Explore, prepare and split in your own environment. EasyDeploy does not profile or split data, so uploading the raw file tells you nothing.

- **Google Sheets link**: download it as CSV from `https://docs.google.com/spreadsheets/d/<id>/export?format=csv`, adding `&gid=<tab id>` for a tab other than the first.
- **Google Drive file link**: `https://drive.google.com/uc?export=download&id=<id>`.
- **Attached file or host connector**: read it directly.

If you cannot reach the data, ask the user to attach the file.

## 3. Explore the real data

Load the real file where you can run code and compute every number; never estimate from a preview. Tell the user about any check you could not do. Check:

- Shape, and what one row represents.
- Types: numbers, categories, dates, free text, ids, numbers stored as text.
- Missing values per column (%).
- The target: class counts and rate, or its distribution and outliers.
- Duplicate rows, and entities appearing more than once.
- Distinct values per text column; single-value columns.
- Obvious ids: unique per row, or named like id, email, phone.
- Date ranges, and whether they cover the period the model will serve.

Give the user a short summary with red flags.

## 4. Leakage checklist

For every column ask: would I know this value at the prediction moment, for a new row? Drop or rebuild it if it is:

- **An identifier**: row id, customer id, email, name, phone, invoice number.
- **A proxy for the target**: a status, reason or score set because of the outcome (cancellation_date, lost_reason).
- **Recorded after the outcome**, or filled in after the prediction moment.
- **An aggregate over the full dataset or future periods**: lifetime totals as of today on an earlier snapshot, or the average target per region.
- **Time leakage**: any value dated after the prediction moment of its row.
- **The same entity twice**: repeated customers let the model memorize them; the split handles this.

A column that predicts the target almost perfectly is a leak until proven otherwise.

## 5. What the platform does, and what you do

EasyDeploy fills missing values (mean for numbers, most frequent value for text), one-hot encodes text columns, and searches and tunes pipelines. A text target, or a numeric target with 10 or fewer distinct values, is treated as classification; any other numeric target is regression.

You do the rest:

- **Joins**: find each file's grain and keys, confirm the join type with the user, and check row counts afterwards.
- **Target**: derive it from the confirmed definition. For a yes/no target use 0/1, with 1 the outcome the user cares about.
- **Leakage and ids**: drop them from the train file.
- **Dates**: turn them into numbers (days since an event, month, day of week). A raw date string is treated as a category.
- **Categories**: group rare values (under about 1% of rows) into "Other"; drop free text with many distinct values.
- **Yes/no columns**: write them as 0 and 1; true/false columns may be dropped from the features.
- **Missing values**: leave the cell empty; placeholders such as "-" or "?" turn a numeric column into text. Add a 0/1 missing flag where missingness may carry signal.

Before applying anything, show the user the drop list and each transformation with its reason; wait for a yes.

## 6. Training-file format

- CSV, comma-separated, UTF-8, one header row, one row per prediction unit.
- Headers: unique; letters, digits and underscores. A header starting with = + - @ or | is escaped on upload and no longer matches your target name.
- The target column, named exactly as you will pass it to `create_model_version`.
- Plain numbers: -0.5, not -.5, which is escaped into text.
- No index column, formulas or line breaks inside cells; at least two columns.
- No ids in the train file. Keep them in the test file (section `split`).
