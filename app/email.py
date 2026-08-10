"""
Generic email delivery via Resend's REST API. Shared by password reset
and the daily digest.

Not best-effort — if an email genuinely fails to send, the caller needs
to know rather than silently pretending it worked.
"""

import os

import requests

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "Skim <noreply@skimstash.com>"


class EmailSendError(Exception):
    pass


def send_email(to: str, subject: str, text: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise EmailSendError("Server is missing RESEND_API_KEY.")

    try:
        response = requests.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"from": FROM_ADDRESS, "to": [to], "subject": subject, "text": text},
            timeout=10,
        )
        response.raise_for_status()
    except Exception as e:
        raise EmailSendError(f"Couldn't send email ({e.__class__.__name__}): {e}")
