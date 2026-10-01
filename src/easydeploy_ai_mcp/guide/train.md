# Train the model

## Project and model

- Call `list_projects`. Reuse a project that matches the problem; otherwise `create_project` with a descriptive name. Share its `ui_url` with the user.
- Call `list_models` in that project. Reuse the model if this is a new version of the same problem; otherwise `create_model` with a name and description that state the target and the unit ("Churn in the next 90 days, per customer"). Every `create_model` call without `model_id` creates another model.

## Model version

Call `create_model_version` with:

- `dataset_version_id`: the **train** dataset's version id. Never the test one; nothing checks this for you.
- `target_feature`: the target header exactly as it appears in `columnNames`. The name is not checked until the job runs, so a mismatch fails training and still spends the credit.

Every other column becomes a feature. Read `columnNames` one last time for ids and leaks.

The task type follows the target: text, or numbers with 10 or fewer distinct values, means classification; numbers with more distinct values mean regression. If that does not match the problem (a count from 0 to 8 that should be regression, or more than 10 numeric class codes), tell the user and change the encoding before training.

## Time-series mode

Set `time_series_mode` to true, with `time_column`, when rows are ordered in time and the model predicts later periods.

- `time_column` is required and must differ from the target.
- Training sorts the rows by that column's raw values and does not use it as a feature. Use a sortable format: ISO dates (2025-03-31) or a number. Put the calendar features you need (month, week, lagged values) in their own columns.
- Cross-validation becomes forward-chaining: each fold trains on earlier rows and scores later ones.
- Your own train/test split must still be chronological.

## Credits and submission

- Call `get_account_status`. Each `submit_training_job` spends one training credit at submission; each batch prediction spends one prediction credit per row. If credits are short, tell the user before you submit.
- Call `submit_training_job` with the `model_version_id`. Omit `dataset_version_id`: the job trains on the model version's own dataset. An explicit one is rejected when its version type is raw, and every upload starts as raw.
- Share the model `ui_url` with the user.

## Waiting

Call `get_training_status` with the `jobId` and wait=true. It blocks for up to `timeout_seconds` (default and maximum 150, so the call ends before the host cuts it off). If it returns `timed_out`, the job is still running: call it again with the same `job_id` to keep waiting. Never resubmit a running job; that starts a second job and spends another credit. Run time depends on the plan and the data and can be much longer than a few minutes. `list_model_versions` and `get_model_version` show the version's `status` (TRAINING_COMPLETED when done).

On FAILED, read the error. Check the target name and the file, fix the cause, and train again.

## Reading the report honestly

Call `get_model_report` (it waits for the report to be ready; `full_report` gives more detail). Read its `metrics` object:

- `crossValidation.score` is the out-of-sample estimate, measured inside the training file: 10-fold stratified, shuffled cross-validation on ROC-AUC for classifiers; 10-fold shuffled cross-validation on negative mean squared error for regressors (the square root of its negative is the RMSE in target units); forward-chaining in time-series mode. The winning pipeline is then refit on all training rows.
- `trainingFit` (accuracy, confusion matrix, per-class precision and recall, ROC-AUC, R²) is measured on the rows the model learned from. It runs high. Never present it as the model's performance.
- Read `warnings` before quoting any number. `metrics` is null when it cannot be derived, and `metricsUnavailableReason` says why.
- The prose summary is written by an LLM. Trust the numbers over the prose.

Tell the user the cross-validation score, what it means, and that the real test comes next. Stop and look for leakage if the cross-validation ROC-AUC is above about 0.95 on a noisy business problem, the training fit is perfect, or one feature dominates the importances. Then read section `validate`.
