import re

with open('aot-group-control/controller.py', 'r') as f:
    content = f.read()

# Remove fallback for Select all in _is_all_selected
content = re.sub(
    r'    if select_all_node is None:\n\s+candidates = _find_by_text_exact\(nodes, "Select all"\)\n\s+if len\(candidates\) == 1:\n\s+select_all_node = candidates\[0\]',
    r'',
    content
)

with open('aot-group-control/controller.py', 'w') as f:
    f.write(content)
