"""
Reset-code delivery via Resend's REST API.

Unlike the YouTube metadata helpers, this is NOT best-effort — if the
email genuinely fails to send, the caller (and therefore the user)
needs to know, rather than silently pretending it worked.
"""

import os

import requests

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "Skim <onboarding@resend.dev>"


class EmailSendError(Exception):
    pass


def send_reset_code(email: str, code: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise EmailSendError("Server is missing RESEND_API_KEY.")

    try:
        response = requests.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": FROM_ADDRESS,
                "to": [email],
                "subject": "Your Skim password reset code",
                "text": (
                    f"Your password reset code is: {code}\n\n"
                    "This code expires in 15 minutes. If you didn't request this, "
                    "you can safely ignore this email."
                ),
            },
            timeout=10,
        )
        response.raise_for_status()
    except Exception as e:
        raise EmailSendError(f"Couldn't send reset email ({e.__class__.__name__}): {e}")
