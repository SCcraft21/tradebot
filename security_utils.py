import logging

def redact_credentials(text: str, credentials: dict) -> str:
    """
    Scans the text for any sensitive credentials and replaces them with '[REDACTED]'.
    """
    if not isinstance(text, str) or not text:
        return text
        
    if not isinstance(credentials, dict):
        return text

    redacted_text = text
    for key, val in credentials.items():
        if not val or not isinstance(val, str):
            continue
        # Avoid redacting short parameters or placeholder strings
        val_clean = val.strip()
        if len(val_clean) <= 4 or val_clean.startswith("YOUR_") or "placeholder" in val_clean.lower():
            continue
        
        # Redact the sensitive value
        redacted_text = redacted_text.replace(val_clean, "[REDACTED]")
        
    return redacted_text

class RedactingFormatter(logging.Formatter):
    """
    A logging formatter that automatically redacts configured credentials from logs.
    """
    def __init__(self, fmt=None, datefmt=None, style='%', credentials=None):
        super().__init__(fmt, datefmt, style)
        self.credentials = credentials if isinstance(credentials, dict) else {}

    def format(self, record):
        original = super().format(record)
        return redact_credentials(original, self.credentials)
