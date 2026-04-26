import re
import os

app_path = 'dashboard/app.py'
with open(app_path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Fix Conflict 1: Remove the head part
content = re.sub(r'<<<<<<< HEAD\n        # ui\.download_button.*?\n=======\n', '', content, flags=re.DOTALL)
content = re.sub(r'>>>>>>> e754900\n', '', content)

# Fix Conflict 2: Prefer theirs (e754900)
# We want the mobility cards AND the following sections
content = re.sub(r'<<<<<<< HEAD\n            ui\.h3\("4\. Retorno Salarial.*?=======\n', '', content, flags=re.DOTALL)
content = re.sub(r'>>>>>>> e754900', '', content)

# Fix Conflict 3: Prefer theirs logic for mobility list
pattern = re.compile(r'<<<<<<< HEAD\n        # --- MOVILIDAD GEOGRÁFICA.*?=======\n(.*?)\n>>>>>>> e754900', re.DOTALL)
content = pattern.sub(r'\1', content)

with open(app_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Conflicts resolved successfully.")
