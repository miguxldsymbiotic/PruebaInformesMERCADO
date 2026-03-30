import polars as pl
import pandas as pd
from pathlib import Path

# Paths manually set for Windows
data_dir = Path(r'c:\Users\ribon\OneDrive - uniminuto.edu\Desarrollos\codigo\InformeShiny\data')

df_ole_salario = pl.read_parquet(data_dir / "df_OLE_Salario_M0.parquet")
df_smmlv = pd.read_excel(data_dir / "SalarioMinimo.xlsx", sheet_name="Series de datos")
df_smmlv_pl = pl.from_pandas(df_smmlv).select([
    pl.col("Año").cast(pl.Int64).alias("anno_corte"),
    pl.col("Salario mínimo mensual").cast(pl.Float64).alias("smmlv")
])

SALARIO_MIDPOINTS = {
    "1 SMMLV": 1.0,
    "Entre 1 y 1,5 SMMLV": 1.25,
    "Entre 1,5 y 2,5 SMMLV": 2.0,
    "Entre 2,5 y 4 SMMLV": 3.25,
    "Entre 4 y 6 SMMLV": 5.0,
    "Entre 6 y 9 SMMLV": 7.5,
    "Más de 9 SMMLV": 9.0
}

# Simular filtrado un snies cualquiera
# Agarrar unos 100 snies para que sea rápido
snies_codigos = df_ole_salario["codigo_snies_del_programa"].unique()[:100]

ole_sal = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))

# Pre-procesamiento
ole_sal = ole_sal.with_columns(
    pl.col("rango_salario").replace(SALARIO_MIDPOINTS, default=1.0).cast(pl.Float64).alias("midpoint")
).join(df_smmlv_pl, on="anno_corte", how="left")

print("Years after join:")
print(ole_sal["anno_corte"].value_counts())

agg_base = ole_sal.group_by(["anno_corte", "codigo_snies_del_programa", "sexo"]).agg([
    ((pl.col("midpoint") * pl.col("graduados_cotizantes_dependientes")).sum() / 
     pl.col("graduados_cotizantes_dependientes").sum()).alias("avg_smmlv_prog"),
    pl.col("smmlv").first().alias("smmlv_yr")
]).filter(pl.col("avg_smmlv_prog").is_not_null())

agg_base = agg_base.with_columns(
    (pl.col("avg_smmlv_prog") * pl.col("smmlv_yr")).alias("salario_pesos")
)

agg_total = agg_base.group_by("anno_corte").agg(pl.col("salario_pesos").mean()).with_columns(pl.lit("TOTAL").alias("label"))

print("Resulting trend data:")
print(agg_total.sort("anno_corte"))
