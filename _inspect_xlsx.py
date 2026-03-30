from pathlib import Path
import pandas as pd

p = Path(r"C:\Users\User\Downloads\proposed_file_naming_20260330.xlsx")
print("exists:", p.exists())
df = pd.read_excel(p)
print("rows:", len(df))
print("columns:", list(df.columns))
need = ["source_relative_path", "proposed_same_folder_path"]
for c in need:
    print(f"has_{c}:", c in df.columns)
print(df[need].head(10).to_string(index=False))
