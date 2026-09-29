# Prepare the data

## 1. Frame the problem first

Before touching the data, agree with the user on:

- **The decision** the model supports, and who acts on it.
- **The target**: what exactly is predicted, and for what unit (one customer, one order, one invoice).
- **The prediction moment**: when the prediction is made. Only information available at that moment may be a feature.
- **The cost of errors**: what a false positive costs compared with a false negative. The decision threshold comes from this.

If the user cannot name the decision, settle that first. Then write the target as one sentence and confirm it, for example: "`churned` is 1 if the customer had no paid activity in the 90 days after the snapshot date, otherwise 0." Leave out rows whose outcome is not known yet (open deals, active loans, customers still in trial) rather than counting them as 0.

## 2. Explore the real data

If you can run code, load the real file there and compute every number; never estimate from a preview. If you cannot, tell the user which checks you could not do. Check:

- Shape, and what one row represents.
- Types: numbers, categories, dates, free text, ids, numbers stored as text.
- Missing values per column (%).
- The target: class counts and rate, or its distribution and outliers.
- Duplicate rows, and entities (customers, machines) appearing more than once.
- Distinct values per text column; columns with a single value.
- Obvious ids: unique per row, or named like id, email, phone, account number.
- The date range of every date column, and whether it covers the period the model will serve.

Give the user a short summary with red flags.

## 3. Leakage checklist

For every column ask: would I know this value at the prediction moment, for a new row? Drop or rebuild it if it is:

- **An identifier**: row id, customer id, email, name, phone, invoice number.
- **A proxy for the target**: a status, reason or score that is set because of the outcome (cancellation_date, lost_reason, a risk score computed from past outcomes).
- **Recorded after the outcome**, or filled in after the prediction moment (a field updated during the follow-up call).
- **An aggregate over the full dataset or future periods**: lifetime totals as of today for an earlier snapshot, average target per region over all rows.
- **Time leakage**: any value dated after the prediction moment of its row.
- **The same entity twice**: repeated customers let the model memorize them; handle this in the split.

A column that predicts the target almost perfectly is a leak until proven otherwise.

## 4. What the platform does, and what you do

EasyDeploy fills missing numbers with the column mean and missing text with the most frequent value, turns each text value into its own indicator column, and searches and tunes pipelines. A text target, or a numeric target with 10 or fewer distinct values, is treated as classification; any other numeric target is regression.

You do the rest:

- **Joins**: find each file's grain and keys, confirm the join type with the user, and compare row counts before and after.
- **Target**: derive it from the confirmed definition. For a yes/no target use 0/1, with 1 the outcome the user cares about.
- **Leakage and ids**: drop them from the train file.
- **Dates**: turn them into numbers (days since an event, tenure, month, day of week). A raw date string is treated as a category.
- **Categories**: group rare values (for example under 1% of rows) into "Other"; drop free text with many distinct values.
- **Yes/no columns**: write them as 0 and 1; a true/false column may be left out of the features.
- **Missing values**: leave the cell empty; placeholders such as "-" or "?" turn a numeric column into text. Add a 0/1 missing flag where missingness may carry signal.

Before applying anything, show the user the drop list and each transformation with its reason, and wait for a yes.

## 5. Training-file format

- CSV, comma-separated, UTF-8, one header row, one row per prediction unit.
- Headers: unique, letters, digits and underscores, no surrounding spaces. The upload validator escapes any cell starting with = + - @ or |, header cells included, so a header like `-score` would no longer match the target name you pass.
- The target column, named exactly as you will pass it to `create_model_version`.
- Plain numbers. Write -0.5, not -.5: a cell starting with "-" and then a non-digit is escaped into text.
- No index column, formulas or line breaks inside cells; at least two columns.
- No ids in the train file. Keep them in the test file (section `split`).
