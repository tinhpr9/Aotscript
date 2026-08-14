import re

with open('aot-group-control/controller.py', 'r') as f:
    content = f.read()

# Remove fallback for Select all (two places)
content = re.sub(
    r'        if select_all_node is None:\n\s+candidates = _find_by_text_exact\(nodes, "Select all"\)\n\s+if len\(candidates\) == 1:\n\s+select_all_node = candidates\[0\]',
    r'',
    content
)

# Remove fallback for Batch actions
content = re.sub(
    r'    if batch_node is None:\n\s+# Fallback text match\n\s+candidates = _find_by_text_exact\(nodes, "Batch actions"\)\n\s+if not candidates:\n\s+candidates = _find_by_text_exact\(nodes, "Batch"\)\n\s+if len\(candidates\) == 1:\n\s+batch_node = candidates\[0\]\n\s+elif len\(candidates\) > 1:\n\s+raise AotControllerError\("batch_actions_selector_ambiguous"\)\n\s+else:\n\s+raise AotControllerError\("batch_actions_not_found"\)',
    r'    if batch_node is None:\n        raise AotControllerError("batch_actions_not_found")',
    content
)

# Remove fallback for Backup item
content = re.sub(
    r'    if backup_item is None:\n\s+candidates = _find_by_text_exact\(nodes, "Backup"\)\n\s+if len\(candidates\) == 0:\n\s+raise AotControllerError\("backup_menu_item_not_found"\)\n\s+if len\(candidates\) > 1:\n\s+raise AotControllerError\("backup_menu_item_ambiguous"\)\n\s+backup_item = candidates\[0\]',
    r'    if backup_item is None:\n        raise AotControllerError("backup_menu_item_not_found")',
    content
)

# Remove fallback for + BACKUP
content = re.sub(
    r'    if final_btn is None:\n\s+candidates = _find_by_text_exact\(nodes, "\+ BACKUP"\)\n\s+if not candidates:\n\s+candidates = _find_by_text_exact\(nodes, "BACKUP"\)\n\s+if len\(candidates\) == 0:\n\s+raise AotControllerError\("final_backup_button_not_found"\)\n\s+if len\(candidates\) > 1:\n\s+raise AotControllerError\("final_backup_button_ambiguous"\)\n\s+final_btn = candidates\[0\]',
    r'    if final_btn is None:\n        raise AotControllerError("final_backup_button_not_found")',
    content
)

with open('aot-group-control/controller.py', 'w') as f:
    f.write(content)
