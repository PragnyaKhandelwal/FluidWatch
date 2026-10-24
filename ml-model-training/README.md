# ML model training artifacts

Training scripts and model files for fluid-level detection from photos.
This folder is reference/provenance - **the actual deployed copy** of the
model in use lives at `server/app/cv/models/`, loaded by
`server/app/cv/real_frame_processor.py`.

## What's here

- **`fill_regression_v2_mobilenet.pth`** + `fill_regression_v2_info.json`
  — **the model actually in use.** MobileNetV3-Small, multitask
  (continuous 0-100% regression + auxiliary 4-class head), trained on
  photos labeled 0/50/80/100%. Test MAE ~3.8%, RMSE ~8.9%. Initialized
  from the classifier below, then fine-tuned - see `train_continuous_v2.py`.
- `fill_classifier_mobilenet.pth` + `fill_classifier_info.json` — the
  earlier 4-class-only classifier (empty/50%/80%/full), 97.4% test
  accuracy. Not deployed directly - the v2 regression model already
  incorporates an equivalent classification head internally. Kept as the
  base the v2 model was built from; see `train_classifier.py`.
- `fluid_line_rf.joblib` + `fluid_line_model.json` — an earlier classical
  CV prototype (edge/color-based heuristic + a small random forest,
  trained on only 8 manually-marked images). Not deployed - superseded by
  the MobileNet models.
- `test_v2.py` — reference inference script this project's integration
  (`real_frame_processor.py`) was adapted from: same architecture, same
  preprocessing.
- `train_classifier.py` / `train_continuous_v2.py` — training scripts, in
  case the model needs retraining on more data later.

## Note

`fill_regression_v2_info.json` flags that intermediate predictions
(anything other than 0/50/80/100%) are interpolation, since training
labels only covered those four discrete levels - worth keeping in mind
when validating against real intermediate-fill photos.
