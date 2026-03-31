# script_converter.py

"""
Utility module for converting OCR extracted text
from modern Malayalam script to old script.

This is used in the OCR pipeline after text is
extracted via Tesseract/Surya OCR, so that both
modern and old script versions can be stored
in the database.
"""

from typing import Dict


# --- Character mapping dictionary ---
# Expand this dictionary with all modern→old mappings you require.
# The following are common Malayalam chillu conversions:
CONVERSION_MAP: Dict[str, str] = {
    "ൻ": "ന്",   # Chillu n → n + virama + ZWJ
    "ർ": "ര്",   # Chillu r → r + virama + ZWJ
    "ൽ": "ല്",   # Chillu l → l + virama + ZWJ
    "ൾ": "ള്",   # Chillu l (retroflex) → l + virama + ZWJ
    "ൿ": "ക്",  
    "റ്റ": "റ്റ്‌ട", # റ്റ (RRA + virama + TA, reformed) → old cluster
    'ക്ക': 'ക്‌ക', # ക്ക (KA + virama + KA)
    'ങ്ങ': 'ങ്‌ങ' ,
    'ത്സ': 'ത്‌സ',
    'മ്പ': 'മ്പ',
    'സ്ത': 'സ്‌ത' ,
    'ക്ഷ': 'ക്‌ഷ',
    'ജ്ഞ': 'ജ്‌ഞ',
    'സ്ര': 'സ്‌ര',
    'പ്ര': 'പ്‌ര',
    'ത്ര': 'ത്‌ര',
    'ബ്ര': 'ബ്‌ര',
    'ത്ര്': 'ത്‌ര്',
    'യാത്ര': 'യാത്‌ര',
    'സ്ത്രി': 'സ്‌ത്രീ',
    'മനുഷ്യൻ': 'മനുഷ്യന്‍',
    'ദ്വ': 'ദ്‌വ',
    'സ്വ': 'സ്‌വ',
    'സ്വസ്ഥ': 'സ്‌വസ്ഥ',
    'സ്വപ്നം': 'സ്‌വപ്നം',
    'ത്മ': 'ത്‌മ',
    'പ്രൗഢ': 'പ്‌രൌഢ',
    'ത്യാഗം': 'ത്‌യാഗം',
    'ക്ക': 'ക്‌ക',
    'ക്രി': 'ക്‌രി',
    'ക്ഷ്മ': 'ക്‌ഷ്മ',
    'ച്ച': 'ച്‌ച',
    'ച്ചൂ': 'ച്‌ചൂ',
    'പ്പ': 'പ്‌പ',
    'ണ്ണ': 'ണ്‌ണ',
    'ഷ്ണ': 'ശ്‌ണ',
    'ന്യ': 'ന്‍യ',
    # Add more mappings here as required for your old script
}


def convert_to_old_script(modern_text: str) -> str:
    """
    Convert OCR extracted modern script text to old script
    using predefined CONVERSION_MAP.

    Args:
        modern_text (str): Text in modern Malayalam script.

    Returns:
        str: Converted text in old Malayalam script.
    """
    if not modern_text:
        return ""

    old_text = modern_text
    for modern, old in CONVERSION_MAP.items():
        old_text = old_text.replace(modern, old)

    return old_text


if __name__ == "__main__":
    # Quick test run
    sample = "കേരളൻ മലയിൽ താമസിക്കുന്നു"
    print("Modern :", sample)
    print("Old    :", convert_to_old_script(sample))
