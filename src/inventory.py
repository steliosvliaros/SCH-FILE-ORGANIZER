from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import pandas as pd


def _series_or_default(df: pd.DataFrame, name: str, default: Any) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([default] * len(df), index=df.index)


def ensure_inventory_schema(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    out = df.copy()
    if out.empty:
        for col in [
            "relative_path",
            "absolute_path",
            "filename",
            "suffix",
            "size_bytes",
            "parent_relative",
            "path_length",
            "filename_length",
            "hash",
        ]:
            if col not in out.columns:
                out[col] = pd.Series(dtype="object")
        return out

    if "relative_path" not in out.columns:
        if "execution_source_relative_path" in out.columns:
            out["relative_path"] = out["execution_source_relative_path"]
        elif "rollback_source_relative_path" in out.columns:
            out["relative_path"] = out["rollback_source_relative_path"]
        else:
            out["relative_path"] = pd.Series([pd.NA] * len(out), index=out.index)

    if "absolute_path" not in out.columns:
        if "execution_source_full_path" in out.columns:
            out["absolute_path"] = out["execution_source_full_path"]
        elif "rollback_source_full_path" in out.columns:
            out["absolute_path"] = out["rollback_source_full_path"]
        else:
            out["absolute_path"] = pd.Series([pd.NA] * len(out), index=out.index)

    rel = out["relative_path"].fillna("").astype(str)
    if "filename" not in out.columns:
        out["filename"] = rel.map(lambda x: PurePosixPath(x.replace('\\', '/')).name if x else "")
    if "suffix" not in out.columns:
        out["suffix"] = out["filename"].map(lambda x: PurePosixPath(str(x)).suffix.lower())
    if "parent_relative" not in out.columns:
        out["parent_relative"] = rel.map(lambda x: str(PurePosixPath(x.replace('\\', '/')).parent) if x else "")
        out.loc[out["parent_relative"].isin([".", ""]), "parent_relative"] = ""
    if "path_length" not in out.columns:
        out["path_length"] = rel.map(lambda x: len(x))
    if "filename_length" not in out.columns:
        out["filename_length"] = out["filename"].fillna("").astype(str).map(len)
    if "size_bytes" not in out.columns:
        out["size_bytes"] = 0
    return out
