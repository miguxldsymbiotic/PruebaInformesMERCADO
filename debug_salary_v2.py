import polars as pl
import pandas as pd
from pathlib import Path

# Configuración de Rutas
data_dir = Path(r'c:\Users\ribon\OneDrive - uniminuto.edu\Desarrollos\codigo\InformeShiny\data')

# 1. Cargar Datos
df_ole_salario = pl.read_parquet(data_dir / "df_OLE_Salario_M0.parquet")
df_snies = pl.read_parquet(data_dir / "df_SNIES_Programas.parquet")
df_smmlv_raw = pd.read_excel(data_dir / "SalarioMinimo.xlsx", sheet_name="Series de datos")
df_smmlv_raw.columns = [c.replace('\xa0', ' ').strip() for c in df_smmlv_raw.columns]
df_smmlv_pl = pl.from_pandas(df_smmlv_raw).select([
    pl.col("Año").cast(pl.Int64).alias("anno_corte"),
    pl.col("Salario mínimo mensual").cast(pl.Float64).alias("smmlv")
])

SALARIO_MIDPOINTS = {
    "1 SMMLV": 1.0, "Entre 1 y 1,5 SMMLV": 1.25, "Entre 1,5 y 2,5 SMMLV": 2.0,
    "Entre 2,5 y 4 SMMLV": 3.25, "Entre 4 y 6 SMMLV": 5.0, "Entre 6 y 9 SMMLV": 7.5,
    "Más de 9 SMMLV": 9.0
}

# 2. Simular filtered_snies (Todos los activos)
snies_codigos = df_snies.filter(pl.col("estado_programa") == "ACTIVO")["codigo_snies_del_programa"].unique()

# 3. Ejecutar Lógica de get_salary_trend_data
df = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))

# JOIN con SMMLV
df = df.with_columns(pl.col("anno_corte").cast(pl.Int64))
df_smmlv_typed = df_smmlv_pl.with_columns(pl.col("anno_corte").cast(pl.Int64))
df = df.join(df_smmlv_typed, on="anno_corte", how="inner")

# Midpoints
df = df.with_columns(
    pl.col("rango_salario").replace(SALARIO_MIDPOINTS, default=1.0).cast(pl.Float64).alias("midpoint")
)

# Nivel 1: Por Programa-Año-Sexo
agg_prog = df.group_by(["anno_corte", "codigo_snies_del_programa", "sexo"]).agg([
    ((pl.col("midpoint") * pl.col("graduados_cotizantes_dependientes")).sum() / 
     pl.col("graduados_cotizantes_dependientes").sum() * pl.col("smmlv").first()).alias("sal_pesos_prog")
]).filter(pl.col("sal_pesos_prog").is_not_null())

# Nivel 2: Promedio de programas
agg_total = agg_prog.group_by("anno_corte").agg(
    pl.col("sal_pesos_prog").mean().alias("salario_pesos")
).with_columns(pl.lit("TOTAL").alias("label"))

agg_sexo = agg_prog.group_by(["anno_corte", "sexo"]).agg(
    pl.col("sal_pesos_prog").mean().alias("salario_pesos")
).rename({"sexo": "label"})

res_pd = pd.concat([
    agg_total.select(["anno_corte", "salario_pesos", "label"]).to_pandas(),
    agg_sexo.select(["anno_corte", "salario_pesos", "label"]).to_pandas()
]).sort_values(["label", "anno_corte"])

print("\n--- TABLA DE EVOLUCIÓN SALARIAL (DEBUG) ---")
print(res_pd.to_string(index=False))
