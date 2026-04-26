import sys

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(len(lines)):
    line = lines[i]
    if "mode='lines+markers'" in line:
        # Determine the format based on the function context or surrounding lines.
        # Desercion uses %{y:.1%}
        # Other use %{y:,.0f}
        
        # Let's search backwards a bit to see if we are in deserción function
        fmt = "%{y:,.0f}"
        for j in range(1, 30):
            if i - j >= 0:
                if 'desercion' in lines[i-j].lower():
                    fmt = "%{y:.1%}"
                    break
                if 'def ' in lines[i-j]:
                    break
        
        new_mode = f"mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='{fmt}'"
        lines[i] = line.replace("mode='lines+markers'", new_mode)

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
