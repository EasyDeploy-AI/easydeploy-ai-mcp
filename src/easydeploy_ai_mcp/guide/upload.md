# Upload the files

Upload each file in its own session: train, test, and validation if you have one. File contents never go into a tool argument or into the conversation, on any channel.

## Channels, in order

1. **Host file parameter.** If your host lets you attach a file to `start_upload`'s `file` parameter, do that. EasyDeploy fetches the bytes itself and the session goes straight to RECEIVING, with no network needed from your sandbox.
2. **Gateway PUT.** Call `start_upload` without `file`. It returns `curl_command`: if you can run shell commands with network access, replace FILE_PATH with the file's path and run it. The gateway takes at most 6 MB and answers 413 above that. Do not split a file into parts.
3. **Share link.** If the command fails with a connection error, your sandbox has no network. If your prepared train or test file is at a Google Sheets or Google Drive link shared with anyone who has the link, or at a file link your host provides, call `upload_from_url` with the `upload_request_id` and that URL. EasyDeploy fetches up to 256 MB. This channel is only for prepared files: a link to raw data is a source to download and prepare first (section `prepare`).
4. **Model builder.** With none of the above: save the file, give it to the user as a download, and ask them to upload it at https://www.easydeploy.ai/model-builder. Then ask for the dataset name they entered or the dataset URL the page shows, find it with `list_datasets`, and check its row count against your file. That upload is already a dataset, so do not call `complete_upload` for it. Never guess which dataset is theirs.

`start_upload` returns `next_steps` with this runbook and the ids filled in. Follow it.

## Finish each upload

Poll `get_upload_status` every few seconds until the status is READY. On REJECTED, read `error`, fix the file, and open a new session with `start_upload`. On EXPIRED, open a new session. Then call `complete_upload` with the project, the `upload_request_id`, a descriptive name ("churn 2025Q3 train") and the `dataset_type`.

## dataset_type

"train", "test" or "validation" is a label for you and the user. EasyDeploy does not enforce it: nothing stops a test dataset from being trained on, so you must pass the right dataset version at every step. The type belongs to the dataset, and a new version of an existing dataset keeps the type the dataset was created with. Upload the test file as its own dataset, never as a new version of the train dataset.

## New dataset or new version

Omit `dataset_id` on `start_upload` to create a new dataset, named by `complete_upload`'s `name`. Pass an existing dataset's id to add a version to it, for example the train file again after you removed a leak; the `name` is then ignored.

## Size caps

- Gateway PUT: 6 MB per file.
- Share link or host file: 256 MB, which is also the validator's limit on every channel.
- Downloading scored output: 9 MB. The output is the input file plus prediction columns, so keep test and scoring files comfortably below that, or split them into several datasets.

## What the validator checks

It rejects empty files, binary files (anything with null bytes, such as Excel workbooks), HTML or XML, JSON or JavaScript, a header row with no comma, and more than 2,000 columns. It strips a UTF-8 byte-order mark. It escapes cells that start with = + - @ or | by prefixing a quote, except numbers such as -1.5 or +2. It does not otherwise rename headers.

## Confirm after upload

- The row count (`rowCount` from `get_upload_status`, data rows without the header) equals your file's row count.
- The dataset version's `columnNames` contain the target name exactly. That list is read with a simple parser that trims spaces and strips leading = + - @ | characters, while training reads the stored file with a full CSV parser. With plain headers the two agree.
- Record the dataset version id of each file. You train on the train version and score the test version.

If anything differs, stop and find out why before training.
