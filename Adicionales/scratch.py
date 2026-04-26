import re

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    code = f.read()

def replacer(m):
    original = m.group(0)
    if 'text=' in original: 
        return original
    y_match = re.search(r'y=([\'\"]\w+[\'\"])', original)
    if y_match:
        y_val = y_match.group(1)
        return original.replace('markers=True', f'text={y_val}, markers=True')
    return original

new_code = re.sub(r'fig\s*=\s*px\.line\([^)]+markers=True\)', replacer, code)

lines = new_code.split('\n')
final_lines = []
for line in lines:
    final_lines.append(line)
    if 'fig = px.line(' in line and 'markers=True' in line:
        indent = line[:len(line) - len(line.lstrip())]
        # Agregamos textposition="top center" para todas las graficas q tengan texto
        final_lines.append(indent + 'fig.update_traces(textposition="top center", textfont=dict(size=11, color="black"), texttemplate="%{y:,.0f}")')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(final_lines))
print("Se actualizaron las graficas en app.py")
