# Split the data

The test file is your only honest measure of how the model does on rows it has never seen. Split before balancing, before computing fill values or category groupings, and before any other step that learns from the data.

## Choose the kind of split

- **Stratified random**: the default for classification when rows are independent and the model will score rows like the ones you have. It keeps the target rate the same in both files.
- **Random**: regression with independent rows.
- **Chronological**: whenever the model predicts the future (next month's churn, a forecast, anything with a snapshot date). Train on the earlier rows, test on the most recent period, and never shuffle across time.
- **Grouped**: whenever one entity (customer, patient, machine, store) has several rows. All rows of an entity go to the same file.

Combine them when both apply, for example grouped by customer and cut by date.

## How much to hold out

- Default: 80% train, 20% test.
- Small data (under about 1,000 rows, or under about 100 rows of the rarer class): hold out 25-30% so the test file has enough of the rarer class to measure anything, and tell the user the estimate will be noisy.
- Large data: a test file of a few thousand rows is enough. Keep its scored output under the 9 MB download cap (see `validate`).

## Rules

- Remove exact duplicate rows before splitting.
- No entity in both files.
- Balancing (SMOTE, oversampling, undersampling) is optional and goes on the train file only, after the split. The model search scores candidates on ROC-AUC, which imbalance does not distort, and you choose the threshold on the holdout anyway, so balance only for severe imbalance and tell the user. Synthetic rows make the report's cross-validation score optimistic and shift the predicted probabilities; the test file stays the judge.
- Anything you compute from the data (fill values, rare-category lists, bin edges) comes from the train file and is applied unchanged to the test file.
- The test file holds real rows only, the target included, with the same feature columns as the train file.
- Keep ids in the test file. Prediction uses only the columns the model was trained on, ignores the rest and returns every input column, so ids let you join results back.
- Optional validation file: when you will compare several models or tune the threshold and still want an untouched final number, split about 60/20/20 into train, validation and test. Make choices on validation, report on test.

## File names

Use names that say what the file is: `churn_2025q3_train.csv`, `churn_2025q3_test.csv`. Add `_balanced` to the train file name only if you balanced it.

## Checks after splitting

- Row counts add up to the deduplicated total.
- Target rate in each file (within about one percentage point for a stratified split).
- Zero overlap of entity ids between the files.
- Chronological split: the latest date in train is not after the earliest date in test.
- Same feature columns in both files, the target present in both, ids only in test.
- No synthetic rows in test.

Show the user the counts and target rates before you upload.
