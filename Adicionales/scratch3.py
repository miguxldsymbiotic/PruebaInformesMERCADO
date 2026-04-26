import sys

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(len(lines)):
    line = lines[i]
    
    # 1. Corrección de la gráfica de Ratio
    if 'y="ratio"' in line and 'px.line' in line:
        # The next line is the fig.update_traces
        if i + 1 < len(lines):
            next_line = lines[i+1]
            if 'texttemplate="%{y:.1%}"' in next_line:
                lines[i+1] = next_line.replace('texttemplate="%{y:.1%}"', 'texttemplate="%{y:,.2f}"')
                
    # 2. Agregar valores a gráficas de barras que faltan (3898, 4906)
    if 'fig = px.bar(' in line:
        if 'text_auto=' not in line:
            # We want to add text_auto='.1%'
            # Since some go to multiple lines, let's see if we can just append it before the ending parenthesis, but barmode='group',\n means it continues.
            # Let's replace 'barmode="group"' or similar.
            pass

# Process bars more robustly
content = "".join(lines)
content = content.replace("orientation='h', barmode='group',\n", "orientation='h', barmode='group', text_auto='.1%',\n")
content = content.replace('orientation="h", barmode="group",\n', 'orientation="h", barmode="group", text_auto=".1%",\n')
# Also check line 3898 and 4906 exact formatting:
# fig = px.bar(df, x="porcentaje", y="rango_salario", color="grupo", orientation='h', barmode='group', 
content = content.replace("orientation='h', barmode='group', ", "orientation='h', barmode='group', text_auto='.1%', ")

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Ratio and Bars updated")
