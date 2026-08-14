with open("tests/test_backup_restore_data.py", "r") as f:
    src = f.read()

src = src.replace(
    'APPS_RESTORE_ACTIVE = _wrap(_node(text="Labels: RESTORE_DATA", clickable=False), _node(text="Batch actions"))',
    'APPS_RESTORE_ACTIVE = _wrap(_node(text="Labels: RESTORE_DATA", clickable=False), _node(text="Batch actions"), _node(text="5 / 5"))'
)
src = src.replace(
    'APPS_RESTORE_INACTIVE = _wrap(_node(text="Batch actions"))',
    'APPS_RESTORE_INACTIVE = _wrap(_node(text="Batch actions"), _node(text="0 / 5"))'
)

user_app_old = """def _user_app_parts(apks_card="[10,10][90,50]", data_card="[10,60][90,100]", restore_btn="[10,110][90,150]"):
    nodes = [_node(text="User app parts", clickable=False)]
    if apks_card:
        nodes.append(_node(text="APKs", bounds=apks_card))
    if data_card:
        nodes.append(_node(text="Data", bounds=data_card))
    if restore_btn:
        nodes.append(_node(text="RESTORE", bounds=restore_btn))
    return _wrap(*nodes)"""

user_app_new = """def _user_app_parts(apks_card="[10,10][90,50]", data_card="[10,60][90,100]", restore_btn="[10,110][90,150]"):
    nodes = [_node(text="User app parts", clickable=False)]
    if apks_card:
        nodes.append(_node(text="APKs", bounds=apks_card))
    if data_card:
        nodes.append(_node(text="Data", bounds=data_card))
    nodes.append(_node(text="Cloud"))
    nodes.append(_node(text="Ext.data"))
    nodes.append(_node(text="Expansion"))
    nodes.append(_node(text="Media"))
    nodes.append(_node(text="Device"))
    if restore_btn:
        nodes.append(_node(text="RESTORE", bounds=restore_btn))
    return _wrap(*nodes)"""

src = src.replace(user_app_old, user_app_new)

with open("tests/test_backup_restore_data.py", "w") as f:
    f.write(src)
print("tests fixed")
