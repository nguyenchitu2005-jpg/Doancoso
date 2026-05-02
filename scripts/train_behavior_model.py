from __future__ import annotations

import argparse
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import joblib
import pandas as pd
import xgboost as xgb
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "datasets" / "Students suspicious behaviors detection dataset fo.zip"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "models" / "suspicious_behavior_model.joblib"


# Khai bao tham so train de co the chay script truc tiep tu terminal.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the suspicious-behavior tabular classifier.")
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET_PATH),
        help="Path to source dataset (.zip or .csv).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output artifact path.",
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
        help="Validation split ratio in (0, 1).",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed.",
    )
    parser.add_argument(
        "--train-on-full-data",
        action="store_true",
        help=(
            "Refit pipeline on 100% data before saving artifact. "
            "If test-size is valid, hold-out metrics are still computed first."
        ),
    )
    parser.add_argument(
        "--strict-doc-schema",
        action="store_true",
        help="Fail train neu schema trich xuat tu .docx khong khop voi CSV.",
    )
    return parser.parse_args()


WORD_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


# DOCX duoc mo nhu mot file zip; ham nay doc phan text thuần de phuc vu doi chieu mo ta dataset.
def extract_docx_text(docx_bytes: bytes) -> str:
    with ZipFile(io.BytesIO(docx_bytes)) as docx_archive:
        if "word/document.xml" not in docx_archive.namelist():
            return ""
        document_xml = docx_archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", WORD_NAMESPACE):
        fragments = [text_node.text for text_node in paragraph.findall(".//w:t", WORD_NAMESPACE) if text_node.text]
        if fragments:
            paragraphs.append("".join(fragments).strip())
    return "\n".join(text for text in paragraphs if text)


# Trich xuat bang trong file Word, vi dataset nay mo ta schema bang DOCX thay vi JSON/schema rieng.
def extract_docx_tables(docx_bytes: bytes) -> list[list[list[str]]]:
    with ZipFile(io.BytesIO(docx_bytes)) as docx_archive:
        if "word/document.xml" not in docx_archive.namelist():
            return []
        document_xml = docx_archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    tables: list[list[list[str]]] = []
    for table in root.findall(".//w:tbl", WORD_NAMESPACE):
        parsed_rows: list[list[str]] = []
        for row in table.findall("./w:tr", WORD_NAMESPACE):
            parsed_cells: list[str] = []
            for cell in row.findall("./w:tc", WORD_NAMESPACE):
                fragments = [text_node.text for text_node in cell.findall(".//w:t", WORD_NAMESPACE) if text_node.text]
                parsed_cells.append(" ".join(fragments).strip())
            if any(parsed_cells):
                parsed_rows.append(parsed_cells)
        if parsed_rows:
            tables.append(parsed_rows)
    return tables


# Chuan hoa cac ten feature duoc nhac den trong van ban/bang mo ta.
def parse_feature_tokens(raw_text: str) -> list[str]:
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_]*", raw_text)
    return [token for token in candidates if "_" in token or token.lower() == "label"]


# Doi chieu cot trong CSV voi glossary/schema doc duoc tu 2 file Word di kem dataset.
def build_doc_schema_report(
    dataframe: pd.DataFrame,
    supplemental_documents: list[dict[str, Any]],
    label_column: str,
) -> dict[str, Any]:
    glossary_entries: dict[str, dict[str, Any]] = {}

    for document in supplemental_documents:
        source_file = str(document.get("file") or "")
        for table in document.get("tables", []):
            if not table:
                continue
            header_cells = [cell.strip().lower() for cell in table[0]]
            if "feature name" not in header_cells or "data type" not in header_cells:
                continue

            feature_index = header_cells.index("feature name")
            datatype_index = header_cells.index("data type")

            for row in table[1:]:
                if len(row) <= max(feature_index, datatype_index):
                    continue
                feature_tokens = parse_feature_tokens(row[feature_index] or "")
                data_type = (row[datatype_index] or "").strip()
                if not feature_tokens:
                    continue
                for feature_name in feature_tokens:
                    glossary_entries[feature_name] = {
                        "feature": feature_name,
                        "data_type": data_type,
                        "source_file": source_file,
                    }

    glossary_features = sorted(glossary_entries.keys())
    expected_feature_columns = sorted([feature for feature in glossary_features if feature != label_column])
    dataframe_columns = dataframe.columns.tolist()
    dataframe_column_set = set(dataframe_columns)

    missing_columns = [column for column in expected_feature_columns if column not in dataframe_column_set]
    extra_columns = [column for column in dataframe_columns if column != label_column and column not in expected_feature_columns]

    type_mismatches: list[dict[str, str]] = []
    for feature_name, entry in glossary_entries.items():
        if feature_name not in dataframe_column_set:
            continue

        declared_type = str(entry.get("data_type") or "").lower()
        series = dataframe[feature_name]
        is_numeric = pd.api.types.is_numeric_dtype(series)
        expected_kind = "unknown"

        if any(token in declared_type for token in ["integer", "float", "double", "number", "binary"]):
            expected_kind = "numeric"
        elif any(token in declared_type for token in ["categorical", "string", "text"]):
            expected_kind = "categorical"

        if expected_kind == "numeric" and not is_numeric:
            type_mismatches.append(
                {
                    "column": feature_name,
                    "expected": str(entry.get("data_type") or "numeric"),
                    "found_dtype": str(series.dtype),
                }
            )
        elif expected_kind == "categorical" and is_numeric:
            type_mismatches.append(
                {
                    "column": feature_name,
                    "expected": str(entry.get("data_type") or "categorical"),
                    "found_dtype": str(series.dtype),
                }
            )

    label_in_glossary = label_column in glossary_entries
    warnings: list[str] = []
    if missing_columns:
        warnings.append(f"Thieu {len(missing_columns)} cot theo Table_1.docx.")
    if type_mismatches:
        warnings.append(f"Co {len(type_mismatches)} cot sai kieu du lieu so voi mo ta trong Word.")
    if not label_in_glossary and glossary_entries:
        warnings.append(f"Cot nhan `{label_column}` khong thay trong bang glossary cua file Word.")
    if not glossary_entries:
        warnings.append("Khong trich xuat duoc glossary schema tu file Word.")

    return {
        "status": "ok" if not warnings else "warning",
        "documents_parsed": len(supplemental_documents),
        "glossary_feature_count": len(glossary_features),
        "expected_feature_columns": expected_feature_columns,
        "missing_columns": missing_columns,
        "extra_columns": extra_columns,
        "type_mismatches": type_mismatches,
        "label_in_glossary": label_in_glossary,
        "warnings": warnings,
    }


# Ho tro 2 kieu dataset:
# - CSV thuan
# - ZIP chua 1 CSV + 2 DOCX mo ta cau truc/du lieu
def load_dataset(dataset_path: Path) -> tuple[pd.DataFrame, str, list[dict[str, Any]]]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Khong tim thay dataset: {dataset_path}")

    if dataset_path.suffix.lower() == ".csv":
        return pd.read_csv(dataset_path), str(dataset_path), []

    if dataset_path.suffix.lower() != ".zip":
        raise ValueError("Chi ho tro dataset dang .csv hoac .zip.")

    with ZipFile(dataset_path) as archive:
        csv_names = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        docx_names = sorted(name for name in archive.namelist() if name.lower().endswith(".docx"))

        if len(csv_names) != 1 or len(docx_names) != 2:
            raise ValueError(
                "Dataset zip phai chua dung 1 file CSV va 2 file DOCX mo ta cau truc. "
                f"Tim thay: {len(csv_names)} CSV, {len(docx_names)} DOCX."
            )

        selected_csv = csv_names[0]
        with archive.open(selected_csv) as csv_handle:
            dataframe = pd.read_csv(csv_handle)

        supplemental_documents: list[dict[str, Any]] = []
        for docx_name in docx_names:
            with archive.open(docx_name) as docx_handle:
                document_bytes = docx_handle.read()
                document_text = extract_docx_text(document_bytes)
                document_tables = extract_docx_tables(document_bytes)
            supplemental_documents.append(
                {
                    "file": docx_name,
                    "char_count": len(document_text),
                    "line_count": len([line for line in document_text.splitlines() if line.strip()]),
                    "content": document_text,
                    "tables": document_tables,
                }
            )

    return dataframe, f"{dataset_path}!/{selected_csv}", supplemental_documents


# Xay dung pipeline sklearn gom:
# - xu ly cot so
# - xu ly cot phan loai
# - XGBoost classifier o cuoi luong
def build_pipeline(
    numeric_columns: list[str],
    categorical_columns: list[str],
    random_state: int,
    scale_pos_weight: float,
) -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                    ]
                ),
                numeric_columns,
            ),
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_columns,
            ),
        ]
    )

    classifier = xgb.XGBClassifier(
        n_estimators=500,
        learning_rate=0.08,
        max_depth=7,
        min_child_weight=1,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        objective="binary:logistic",
        eval_metric="auc",
        random_state=random_state,
        n_jobs=-1,
        tree_method="hist",
        verbosity=0,
    )

    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("classifier", classifier),
        ]
    )


# Tinh bo metric giu lai trong artifact de backend co the hien thi lai sau khi train.
def compute_metrics(y_true: pd.Series, y_pred: Any, y_score: Any) -> dict[str, Any]:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred)), 4),
        "recall": round(float(recall_score(y_true, y_pred)), 4),
        "f1": round(float(f1_score(y_true, y_pred)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, y_score)), 4),
        "confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
    }


def main() -> None:
    # 1) Nap dataset va kiem tra cot nhan.
    args = parse_args()
    dataset_path = Path(args.dataset)
    output_path = Path(args.output)

    dataframe, dataset_source, supplemental_documents = load_dataset(dataset_path)
    if args.label_column not in dataframe.columns:
        raise ValueError(f"Khong tim thay cot nhan `{args.label_column}` trong dataset.")
    doc_schema_report = build_doc_schema_report(
        dataframe=dataframe,
        supplemental_documents=supplemental_documents,
        label_column=args.label_column,
    )
    if args.strict_doc_schema and doc_schema_report["status"] != "ok":
        raise ValueError(
            "Schema tu Word khong khop CSV. "
            f"Warnings: {' | '.join(doc_schema_report.get('warnings', []))}"
        )

    # 2) Tach feature/label va xac dinh cot so - cot phan loai.
    feature_columns = [column for column in dataframe.columns if column != args.label_column]
    features = dataframe[feature_columns]
    target = dataframe[args.label_column].astype(int)

    numeric_columns = features.select_dtypes(include=["number"]).columns.tolist()
    categorical_columns = [column for column in feature_columns if column not in numeric_columns]
    class_counts = target.value_counts().to_dict()
    negative_count = float(class_counts.get(0, 1))
    positive_count = float(class_counts.get(1, 1))
    scale_pos_weight = max(1.0, negative_count / max(1.0, positive_count))
    pipeline = build_pipeline(
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        random_state=args.random_state,
        scale_pos_weight=scale_pos_weight,
    )

    # 3) Train voi hold-out de lay metric danh gia ban dau.
    training_mode = "holdout_only"
    if 0.0 < args.test_size < 1.0:
        X_train, X_test, y_train, y_test = train_test_split(
            features,
            target,
            test_size=args.test_size,
            random_state=args.random_state,
            stratify=target,
        )
        pipeline.fit(X_train, y_train)
        y_score = pipeline.predict_proba(X_test)[:, 1]
        y_pred = (y_score >= 0.5).astype(int)
        metrics = compute_metrics(y_true=y_test, y_pred=y_pred, y_score=y_score)
    else:
        metrics = {
            "note": "Khong co hold-out metrics vi test-size khong nam trong khoang (0, 1).",
        }

    # 4) Neu duoc yeu cau, fit lai tren 100% du lieu truoc khi luu artifact.
    if args.train_on_full_data:
        pipeline.fit(features, target)
        training_mode = "full_data_refit_after_evaluation"

    # 5) Dong goi toan bo metadata can thiet de backend co the load va giai thich model.
    artifact = {
        "pipeline": pipeline,
        "feature_columns": feature_columns,
        "numeric_columns": numeric_columns,
        "categorical_columns": categorical_columns,
        "label_column": args.label_column,
        "dataset_source": dataset_source,
        "supplemental_documents": supplemental_documents,
        "supplemental_document_count": len(supplemental_documents),
        "doc_schema_report": doc_schema_report,
        "row_count": int(len(dataframe)),
        "class_balance": target.value_counts().sort_index().to_dict(),
        "metrics": metrics,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "model_type": "XGBoost",
        "train_on_full_data": bool(args.train_on_full_data),
        "training_mode": training_mode,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_path)

    # 6) In thong tin chinh ra stdout de de kiem tra sau khi train bang terminal.
    print(f"Saved model artifact to: {output_path}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if doc_schema_report.get("warnings"):
        print(json.dumps({"doc_schema_warnings": doc_schema_report["warnings"]}, ensure_ascii=False))
    print(
        json.dumps(
            {
                "training_mode": training_mode,
                "train_on_full_data": bool(args.train_on_full_data),
                "rows": int(len(dataframe)),
                "supplemental_document_count": len(supplemental_documents),
                "doc_schema_status": doc_schema_report.get("status"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
