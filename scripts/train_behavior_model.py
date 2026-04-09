from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import joblib
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the suspicious-behavior tabular classifier.")
    parser.add_argument(
        "--dataset",
        default=r"d:\Students suspicious behaviors detection dataset fo.zip",
        help="Path to the source dataset (.zip or .csv).",
    )
    parser.add_argument(
        "--output",
        default="models/suspicious_behavior_model.joblib",
        help="Where to store the trained model artifact.",
    )
    parser.add_argument(
        "--label-column",
        default="label",
        help="Target label column name in the dataset.",
    )
    parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Validation split ratio.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for reproducibility.",
    )
    return parser.parse_args()


def load_dataset(dataset_path: Path) -> tuple[pd.DataFrame, str]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Khong tim thay dataset: {dataset_path}")

    if dataset_path.suffix.lower() == ".csv":
        return pd.read_csv(dataset_path), str(dataset_path)

    if dataset_path.suffix.lower() != ".zip":
        raise ValueError("Chi ho tro dataset dang .csv hoac .zip.")

    with ZipFile(dataset_path) as archive:
        csv_names = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        if not csv_names:
            raise ValueError("Khong tim thay file CSV ben trong file zip.")
        selected_csv = csv_names[0]
        with archive.open(selected_csv) as csv_handle:
            dataframe = pd.read_csv(csv_handle)
    return dataframe, f"{dataset_path}!/{selected_csv}"


def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset)
    output_path = Path(args.output)

    dataframe, dataset_source = load_dataset(dataset_path)
    if args.label_column not in dataframe.columns:
        raise ValueError(f"Khong tim thay cot nhan `{args.label_column}` trong dataset.")

    X = dataframe.drop(columns=[args.label_column])
    y = dataframe[args.label_column].astype(int)

    # Tự động encode categorical columns
    cat_cols = X.select_dtypes(include=['object']).columns
    for col in cat_cols:
        X[col] = LabelEncoder().fit_transform(X[col].astype(str))

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=args.random_state, stratify=y
    )

    # ====================== XGBoost Model ======================
    scale_pos_weight = len(y_train[y_train == 0]) / len(y_train[y_train == 1])
    
    model = xgb.XGBClassifier(
        n_estimators=500,
        learning_rate=0.08,
        max_depth=7,
        min_child_weight=1,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        eval_metric="auc",
        random_state=args.random_state,
        n_jobs=-1,
        tree_method="hist",
        verbosity=1,
    )

    model.fit(X_train, y_train)

    # ====================== Evaluation ======================
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()
    metrics = {
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred)), 4),
        "recall": round(float(recall_score(y_test, y_pred)), 4),
        "f1": round(float(f1_score(y_test, y_pred)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, y_proba)), 4),
        "confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
    }

    # ====================== Save Artifact ======================
    artifact = {
        "model": model,
        "feature_columns": X.columns.tolist(),
        "metrics": metrics,
        "dataset_source": dataset_source,
        "row_count": int(len(dataframe)),
        "class_balance": y.value_counts().sort_index().to_dict(),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "model_type": "XGBoost",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_path)

    print(f"✅ Model XGBoost da train xong!")
    print(f"Luu tai: {output_path}")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
