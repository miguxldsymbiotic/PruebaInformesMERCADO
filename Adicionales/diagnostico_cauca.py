import polars as pl

# 1. Base OLE CAUCA 2022
ole = pl.read_parquet('data/df_OLE_Movilidad_M0.parquet')
ole_cauca = ole.filter(
    (pl.col('anno_corte') == 2022) & 
    (pl.col('divipola_mpio_principal').cast(pl.Int64) >= 19000) & 
    (pl.col('divipola_mpio_principal').cast(pl.Int64) <= 19999)
)
print(f"Total inicial OLE CAUCA 2022: {ole_cauca['graduados'].sum()}")

# 2. Cruce con df_snies
snies = pl.read_parquet('data/df_SNIES_Programas.parquet')
snies = snies.unique()

snies_ole = ole_cauca["codigo_snies_del_programa"].unique().to_list()
snies_master = snies["codigo_snies_del_programa"].unique().to_list()

missing_in_master = [s for s in snies_ole if s not in snies_master]
graduados_missing_master = ole_cauca.filter(pl.col("codigo_snies_del_programa").is_in(missing_in_master))["graduados"].sum()

print(f"Graduados perdidos por SNIES no existente en maestro SNIES_Programas: {graduados_missing_master}")

ole_cauca = ole_cauca.filter(~pl.col("codigo_snies_del_programa").is_in(missing_in_master))
print(f"Subtotal después de cruzar con maestro SNIES: {ole_cauca['graduados'].sum()}")

# 3. Filtro reconocimiento ministerio (el original que comentamos recientemente)
snies_reconocidos = snies.filter(
    pl.col("reconocimiento_del_ministerio").is_not_null() & 
    (pl.col("reconocimiento_del_ministerio") != "") &
    (pl.col("reconocimiento_del_ministerio") != "Sin dato")
)["codigo_snies_del_programa"].unique().to_list()

missing_reconocimiento = [s for s in ole_cauca["codigo_snies_del_programa"].unique().to_list() if s not in snies_reconocidos]
graduados_missing_rec = ole_cauca.filter(pl.col("codigo_snies_del_programa").is_in(missing_reconocimiento))["graduados"].sum()
print(f"Graduados perdidos por filtro 'reconocimiento_del_ministerio' nulo/indefinido: {graduados_missing_rec}")

# 4. Cruce con df_Cobertura_distinct.parquet (Sidebar geo-filter)
cob = pl.read_parquet('data/df_Cobertura_distinct.parquet')
cob_cauca = cob.filter(pl.col("departamento").str.to_uppercase() == "CAUCA" if "departamento" in cob.columns else pl.col("departamento_oferta").str.to_uppercase() == "CAUCA")
snies_cob_cauca = cob_cauca["codigo_snies_del_programa"].unique().to_list()

missing_in_cob = [s for s in ole_cauca["codigo_snies_del_programa"].unique().to_list() if s not in snies_cob_cauca]
graduados_missing_cob = ole_cauca.filter(pl.col("codigo_snies_del_programa").is_in(missing_in_cob))["graduados"].sum()

print(f"Graduados perdidos porque su SNIES NO tiene oferta en 'CAUCA' según df_Cobertura_distinct: {graduados_missing_cob}")

ole_cauca_final = ole_cauca.filter(~pl.col("codigo_snies_del_programa").is_in(missing_in_cob))
print(f"Subtotal final que ve el Dashboard (sin validar filtro minedu): {ole_cauca_final['graduados'].sum()}")

