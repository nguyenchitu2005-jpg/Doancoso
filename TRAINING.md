# Hybrid behavior model

`Students suspicious behaviors detection dataset fo.zip` is a tabular dataset, not a YOLO image dataset.

This project now supports a second classifier trained from that CSV and used in parallel with YOLO during review:

- YOLO handles object evidence such as `person` and `cell phone`.
- The tabular classifier adds a suspiciousness score from frame-level features compatible with the dataset schema.
- If the dataset is a `.zip`, the training script also reads any `.docx` files (for example `Table_1.docx`, `Table_2.docx`) and stores their extracted text in the model artifact metadata.
- The script parses `Feature Name` and `Data Type` from Word tables to validate CSV schema before training, then stores a `doc_schema_report` in the artifact.
- Both outputs are merged into the same review timeline.

## Train

```powershell
python scripts/train_behavior_model.py
```

Use strict schema validation if you want training to fail on mismatch between CSV and Word glossary:

```powershell
python scripts/train_behavior_model.py --strict-doc-schema
```

The default dataset location is now:

```text
data/datasets/Students suspicious behaviors detection dataset fo.zip
```

If you want to use a different source file, you can still override it:

```powershell
python scripts/train_behavior_model.py --dataset "path/to/your-dataset.zip"
```

The trained artifact is stored at `models/suspicious_behavior_model.joblib`.

`DetectionService` will auto-load that artifact during the next video review run.

## MediaPipe pipeline

The review pipeline now also uses:

- `MediaPipe Face Mesh` for face landmarks, iris, gaze, and head pose estimation.
- `MediaPipe Hands` for hand count and hand-phone interaction cues.

These features are merged with YOLO detections before the behavior model scores each sampled frame.
