ROLES = {
    "AXApplication": "application",
    "AXWindow": "window",
    "AXButton": "button",
    "AXCheckBox": "checkbox",
    "AXRadioButton": "radio button",
    "AXTextField": "text field",
    "AXTextArea": "text field",
    "AXStaticText": "text",
    "AXLink": "link",
    "AXMenu": "menu",
    "AXMenuItem": "menu item",
    "AXMenuBar": "menu bar",
    "AXList": "list",
    "AXRow": "list item",
    "AXTable": "table",
    "AXGroup": "group",
    "AXScrollArea": "scroll area",
    "AXComboBox": "combobox",
    "AXPopUpButton": "combobox",
    "AXSlider": "slider",
}


def normalize(role: str) -> str:
    return ROLES.get(role, "unknown")
