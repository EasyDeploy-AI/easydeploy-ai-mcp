# Validate on the holdout

The test file tells you how the model does on rows it never saw. Do this before you present any result as usable.

## Score the test file

1. Call `run_batch_prediction` with the model version id and the **test** dataset version id. It costs one prediction credit per row and returns a `prediction_id` right away (or pass `wait_for_result` to block).
2. Call `get_prediction` until the status is COMPLETED. A completed batch comes with `curl_command` and `download_url`. The download token works once and expires after 15 minutes, so download promptly; if the download fails, call `get_prediction` again for a fresh one. If you cannot run commands with network access, give the user the `ui_url`, ask them to download the CSV there and share it with you.
3. The file is your test file in its original row order, every column kept (target and ids included), plus a `prediction` column and, for classifiers, one probability column per class. For a 0/1 target, `probability_1` is the probability of class 1. For a text target the columns are numbered by class in sorted label order: `probability_0` is the alphabetically first label.

Scored output above 9 MB cannot be downloaded; split the test file into smaller datasets. A FAILED job that names a missing feature means the test file lacks a training column: fix the file and score it again.

## Compute the metrics yourself

EasyDeploy does not return holdout metrics. If you can run code, compute them from the downloaded file; never estimate them.

**Classification**

- Confusion matrix, precision, recall and F1 for the positive class, first at the model's default rule (the `prediction` column).
- ROC-AUC from the probability column, plus precision-recall AUC when positives are rare. For more than two classes: one-vs-rest ROC-AUC and per-class precision and recall.
- A threshold sweep from about 0.05 to 0.95: at each threshold, precision, recall, the number of rows flagged, and the expected cost from the user's cost of a false positive versus a false negative. Choose the threshold with the lowest cost, or the one that fits the user's capacity ("we can call 200 accounts a week"). Fall back to the best F1 only when the costs are unknown, and say so. Never assume 0.5.
- A baseline: the positive rate, and what always predicting the majority class would score.

**Regression**

- MAE, RMSE and R², in target units, next to the baseline of always predicting the train mean.
- The error distribution: percentiles of absolute error, the mean error (bias), the worst cases, and errors by segment or by size of the target. Give the user a plain margin: "8 in 10 predictions are within ±X."

Choosing the threshold on the test file uses it up a little: the numbers at that threshold are slightly optimistic. With a validation file, choose there and report on test.

## Compare with the training report

- Holdout close to the cross-validation score (within a few points): consistent.
- Holdout much worse: overfitting, drift between periods, a random cross-validation judged against a time-based test, or a leak that exists in the training rows but not in the test rows.
- Holdout near perfect (ROC-AUC or R² around 0.98 or higher on a noisy business problem): suspect leakage. Look at the most important features and at any column that could encode the outcome.

## Go or no-go

Tell the user, in plain language:

- How good the model is, in their terms: "Of every 100 accounts flagged at this threshold, about 62 churn, and it catches 7 in 10 of the accounts that churn."
- Whether it beats the baseline and their current way of deciding.
- The threshold you recommend, and why.
- The limits: size of the test file, the period it covers, segments where it is weak.
- Your recommendation: go, no-go, or go with conditions.

## When the model is weak

Say so plainly; do not oversell. Propose changes, confirm them with the user, then train a new model version (another training credit) and validate again:

- More rows, a longer history, more examples of the rarer class.
- Better features: recent activity windows, ratios, recency, tenure, parsed dates, data joined from other sources.
- Less noise: remove leaky, id-like, near-constant and free-text columns.
- A clearer target, or ambiguous rows left out.
- A different framing: another prediction moment or horizon.

Every round judged on the same test file makes it a little less honest. After several rounds, hold out a fresh test file if the data allows.
