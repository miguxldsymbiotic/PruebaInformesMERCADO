import sys
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

old_trace = 'fig.update_traces(textposition="top center", textfont=dict(size=11, color="black"), texttemplate="%{y:,.0f}")'

for i in range(len(lines)):
    line = lines[i]
    if old_trace in line:
        prev_line = lines[i-1]
        
        # Decide format based on y=...
        fmt = '%{y:,.0f}'
        if any(col in prev_line for col in ['y="tasa"', 'y="retencion"', 'y="ratio"', 'y="desercion_anual_mean"', 'y="participacion"']):
            fmt = '%{y:.1%}'
            
        # Ensure mode="lines+markers+text" so text is forced to render
        new_trace = f'fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="{fmt}")'
        lines[i] = line.replace(old_trace, new_trace)

# There are also some plots in app.py that missed the initial regex because they didn't have text="...", let's add them.
# The list of functions that need text added are the calc_plot_ ones where we didn't inject text=
# Actually, setting mode="lines+markers+text" forces the labels IF we set texttemplate="%{y...}". Wait, no, if mode="lines+markers+text" AND texttemplate is present, it uses texttemplate, NO NEED for text= parameter in px.line!

# If any px.line markers=True lines are around and lack our update_traces entirely, we inject it.
# We already injected old_trace across 24 plots. Let's make sure SNIES plots have them!
# Wait, were lines 2697, 2718, 2743, 2764, etc. injected before? Yes, `grep_search` found them!
# So ALL 24 line plots have `old_trace` below them.

# write back
with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write("".join(lines))
print("Se actualizaron los formatos % y mode en app.py")
