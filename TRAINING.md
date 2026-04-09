# Hybrid behavior model

`Students suspicious behaviors detection dataset fo.zip` is a tabular dataset, not a YOLO image dataset.

This project now supports a second classifier trained from that CSV and used in parallel with YOLO during review:

- YOLO handles object evidence such as `person` and `cell phone`.
- The tabular classifier adds a suspiciousness score from frame-level features compatible with the dataset schema.
- Both outputs are merged into the same review timeline.

## Train

```powershell
python scripts/train_behavior_model.py --dataset "d:\Students suspicious behaviors detection dataset fo.zip"
```

The trained artifact is stored at `models/suspicious_behavior_model.joblib`.

`DetectionService` will auto-load that artifact during the next video review run.

## MediaPipe pipeline

The review pipeline now also uses:

- `MediaPipe Face Mesh` for face landmarks, iris, gaze, and head pose estimation.
- `MediaPipe Hands` for hand count and hand-phone interaction cues.

These features are merged with YOLO detections before the behavior model scores each sampled frame.
