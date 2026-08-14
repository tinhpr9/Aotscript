import re

with open('aot-group-control/controller.py', 'r') as f:
    content = f.read()

# Remove fallback for Apply
content = re.sub(
    r'        if apply_node is None:\n\s+# Fallback: look for text "Apply" or "OK"\n\s+for text in \("Apply", "OK", "Done"\):\n\s+candidates = _find_by_text_exact\(nodes, text\)\n\s+if len\(candidates\) == 1:\n\s+apply_node = candidates\[0\]\n\s+break',
    r'',
    content
)

with open('aot-group-control/controller.py', 'w') as f:
    f.write(content)
