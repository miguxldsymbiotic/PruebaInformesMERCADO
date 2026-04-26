import polars as pl
from pathlib import Path

data_dir = Path("data")

df_saber = pl.read_parquet(data_dir / "df_SaberPRO.parquet").with_columns([
    pl.col("codigo_snies_del_programa").cast(pl.Int64),
    pl.col("pro_gen_estu_edad").cast(pl.Float64)
]).with_columns(
    pl.when((pl.col("pro_gen_estu_edad") >= 15) & (pl.col("pro_gen_estu_edad") < 25)).then(pl.lit("15 - 24"))
    .when((pl.col("pro_gen_estu_edad") >= 25) & (pl.col("pro_gen_estu_edad") < 35)).then(pl.lit("25 - 34"))
    .when((pl.col("pro_gen_estu_edad") >= 35) & (pl.col("pro_gen_estu_edad") < 45)).then(pl.lit("35 - 44"))
    .when((pl.col("pro_gen_estu_edad") >= 45) & (pl.col("pro_gen_estu_edad") < 55)).then(pl.lit("45 - 54"))
    .when((pl.col("pro_gen_estu_edad") >= 55) & (pl.col("pro_gen_estu_edad") <= 100)).then(pl.lit("55 - 100"))
    .otherwise(pl.lit("ND")).alias("grupo_edad")
)

print("Distribucion grupo_edad:")
dist = df_saber.group_by("grupo_edad").len().sort("grupo_edad")
for row in dist.iter_rows():
    print(f"{row[0]}: {row[1]}")

print("\nValidacion rangos:")
ranges = df_saber.group_by("grupo_edad").agg([
    pl.col("pro_gen_estu_edad").min().alias("min_e"),
    pl.col("pro_gen_estu_edad").max().alias("max_e")
]).sort("grupo_edad")
for row in ranges.iter_rows():
    print(f"{row[0]} -> min: {row[1]}, max: {row[2]}")
