# Predict with the model

A model version can score as soon as its status is TRAINING_COMPLETED. There is no separate deployment step: `run_prediction` and `run_batch_prediction` run the model directly. Use it for real decisions only after it has passed section `validate`.

## What new data must look like

- Every feature column the model was trained on, with the same name, meaning, units and encoding, and the same transformations (parsed dates, grouped categories, 0/1 flags) using the values you computed from the train file. Build it with the same code you used for the training file.
- Only information available at the prediction moment.
- Extra columns are fine: ids, names, even the target. They are ignored for scoring and returned in the output.
- A missing training column fails the job, and the error names the column.
- Category values the model never saw are accepted and treated as none of the known ones. Count how many rows have them and tell the user if there are many.

## One record: `run_prediction`

Pass the `model_version_id` and `input_data`: an object with every feature name and its value, numbers as numbers and text as text. It costs one prediction credit and returns the predicted label or value in `output`. It returns no probability, so the model's default rule decides the label. When the decision depends on the threshold you chose, score the records with `run_batch_prediction` instead, even if there are only a few.

## A file: `run_batch_prediction`

1. Build the scoring file like the test file: same feature columns, ids kept.
2. Upload it as its own dataset (see section `upload`). `dataset_type` has no scoring value; use "test" and a name that says it is a scoring file, such as "churn scoring 2025-10".
3. Check credits with `get_account_status`: one prediction credit per row.
4. Call `run_batch_prediction` with the model version id and the scoring dataset version id, then `get_prediction` until COMPLETED, and download the file once, promptly (the token works once and expires after 15 minutes).
5. Scored output above 9 MB cannot be downloaded: split large files into several datasets and score each.

`list_predictions` lists earlier runs if you need to find one again.

## Apply the chosen threshold

For a classifier, apply the threshold from section `validate` to the probability column, for example flag a row when `probability_1` is at or above the threshold. Do not use the `prediction` column for decisions when you chose a threshold other than the model's default. For a regressor, report each prediction with the error margin measured on the holdout.

## Build outputs from real predictions only

- Suggest a few options first (a ranked list, a summary by segment, a dashboard, a report) and confirm with the user before you build one.
- Build every list, chart and number from the downloaded prediction file, joined back to the records by id. No invented rows, no illustrative values, no placeholder records.
- State next to the output which model version produced it, the threshold used, and its holdout performance.
- If a prediction job failed or a file is missing, say so instead of filling the gap.

## Keep it honest over time

When real outcomes arrive for rows you scored, compare them with the predictions. If performance drops, or the data starts to look different from the training data, tell the user and retrain on fresh data with a fresh test file.
