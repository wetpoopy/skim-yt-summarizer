"""Reset-code delivery — thin wrapper around the shared email sender."""

from app.email import EmailSendError, send_email

__all__ = ["EmailSendError", "send_reset_code"]


def send_reset_code(email: str, code: str) -> None:
    send_email(
        to=email,
        subject="Your Skim password reset code",
        text=(
            f"Your password reset code is: {code}\n\n"
            "This code expires in 15 minutes. If you didn't request this, "
            "you can safely ignore this email."
        ),
    )
