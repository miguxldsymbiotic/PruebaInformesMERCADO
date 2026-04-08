import polars as pl
from pathlib import Path
import scipy.stats as stats

app_dir = Path(__file__).parent
data_dir = app_dir.parent / "data"

print("Cargando datos...")
df_snies = pl.read_parquet(data_dir / "df_SNIES_Programas.parquet").unique()
df_snies = df_snies.filter(
    pl.col("reconocimiento_del_ministerio").is_not_null() & 
    (pl.col("reconocimiento_del_ministerio") != "") &
    (pl.col("reconocimiento_del_ministerio") != "Sin dato")
)

df_pcurso = pl.read_parquet(data_dir / "df_PCurso_agg.parquet")
df_matricula = pl.read_parquet(data_dir / "df_Matricula_agg.parquet")
df_graduados = pl.read_parquet(data_dir / "df_Graduados_agg.parquet")
df_desercion = pl.read_parquet(data_dir / "df_SPADIES_Desercion.parquet").filter(
    (pl.col("codigo_snies_del_programa") < 1_000_000) &
    pl.col("desercion_anual_mean").is_not_null() &
    pl.col("desercion_anual_mean").is_not_nan()
).with_columns([
    pl.col("codigo_snies_del_programa").cast(pl.Int64),
    pl.col("anno").cast(pl.Int32)
])

print("Calculando formula de deserción proxy...")
_df_m_agg = df_matricula.with_columns([pl.col("codigo_snies_del_programa").cast(pl.Int64), pl.col("anno").cast(pl.Int32)]).group_by(["codigo_snies_del_programa", "anno"]).agg(pl.col("matricula_sum").sum().alias("matriculados"))
_df_g_agg = df_graduados.with_columns([pl.col("codigo_snies_del_programa").cast(pl.Int64), pl.col("anno").cast(pl.Int32)]).group_by(["codigo_snies_del_programa", "anno"]).agg(pl.col("graduados_sum").sum().alias("graduados"))
_df_p_agg = df_pcurso.with_columns([pl.col("codigo_snies_del_programa").cast(pl.Int64), pl.col("anno").cast(pl.Int32)]).group_by(["codigo_snies_del_programa", "anno"]).agg(pl.col("primer_curso_sum").sum().alias("primer_curso"))

_df_universe = pl.concat([
    _df_m_agg.select(["codigo_snies_del_programa", "anno"]),
    _df_g_agg.select(["codigo_snies_del_programa", "anno"]),
    _df_p_agg.select(["codigo_snies_del_programa", "anno"])
]).unique()

_df_proxy = _df_universe.join(_df_m_agg, on=["codigo_snies_del_programa", "anno"], how="left") \
                        .join(_df_p_agg, on=["codigo_snies_del_programa", "anno"], how="left") \
                        .join(_df_g_agg, on=["codigo_snies_del_programa", "anno"], how="left") \
                        .fill_null(0).sort(["codigo_snies_del_programa", "anno"])

_df_proxy = _df_proxy.with_columns(pl.col("matriculados").shift(1).over("codigo_snies_del_programa").alias("matriculados_t_1"))
_df_proxy = _df_proxy.with_columns(poblacion_riesgo=(pl.col("matriculados_t_1") + pl.col("primer_curso"))) \
                     .filter(pl.col("matriculados_t_1").is_not_null() & (pl.col("poblacion_riesgo") > 0)) \
                     .with_columns(desercion_calculada=((pl.col("matriculados_t_1") + pl.col("primer_curso") - pl.col("graduados") - pl.col("matriculados")) / pl.col("poblacion_riesgo"))) \
                     .with_columns(pl.col("desercion_calculada").clip(lower_bound=0.0, upper_bound=1.0).cast(pl.Float64)) \
                     .select(["codigo_snies_del_programa", "anno", "desercion_calculada"])

print("Cruzando y exportando...")
df_compare = df_desercion.select([
    "codigo_snies_del_programa", "anno", pl.col("desercion_anual_mean").alias("desercion_real")
]).join(_df_proxy, on=["codigo_snies_del_programa", "anno"], how="inner")

df_snies_info = df_snies.with_columns(pl.col("codigo_snies_del_programa").cast(pl.Int64)).select([
    "codigo_snies_del_programa", "programa_academico", "nombre_institucion", "nivel_de_formacion"
]).unique("codigo_snies_del_programa")

df_compare = df_compare.join(df_snies_info, on="codigo_snies_del_programa", how="left")

df_compare = df_compare.select([
    "codigo_snies_del_programa", "programa_academico", "nombre_institucion", "nivel_de_formacion",
    "anno", "desercion_real", "desercion_calculada"
]).with_columns(
    error_absoluto=(pl.col("desercion_real") - pl.col("desercion_calculada")).abs()
)

out_path = data_dir / "Comparacion_Desercion_Calculada.xlsx"
df_compare.write_excel(out_path)
print(f"Excel exportado a: {out_path}")

print("\nAnalizando nivel UNIVERSITARIO...")
# Identificar como filtrarlo
df_univ = df_compare.filter(pl.col("nivel_de_formacion").str.to_uppercase() == "UNIVERSITARIO")

if df_univ.height > 0:
    mean_real = df_univ["desercion_real"].mean()
    mean_calc = df_univ["desercion_calculada"].mean()
    mae = df_univ["error_absoluto"].mean()
    rmse = ((df_univ["desercion_real"] - df_univ["desercion_calculada"]) ** 2).mean() ** 0.5
    
    # Calcular media para mae y variables que esten validas (sin nans en esa franja)
    df_univ_valid = df_univ.drop_nulls(subset=["desercion_real", "desercion_calculada"])
    
    if df_univ_valid.height > 1:
        pearson_r, _ = stats.pearsonr(df_univ_valid["desercion_real"].to_list(), df_univ_valid["desercion_calculada"].to_list())
    else:
        pearson_r = 0.0

    print("\n" + "="*50)
    print(" REPORTE ESTADISTICO DISCREPANCIA (UNIVERSITARIO)")
    print("="*50)
    print(f"Registros comparables analizados: {df_univ.height}")
    print(f"Promedio Deserción Real (SPADIES): {mean_real*100:.2f}%")
    print(f"Promedio Deserción Calculada:        {mean_calc*100:.2f}%")
    print("-" * 50)
    print(f"Error Absoluto Medio (MAE):          {mae*100:.2f} pp")
    print(f"Raíz del Error Cuadrático (RMSE):    {rmse*100:.2f} pp")
    print(f"Correlación de Pearson (r):          {pearson_r:.4f}")
    print("="*50)
else:
    print("No se encontraron registros de nivel UNIVERSITARIA para comparar.")
