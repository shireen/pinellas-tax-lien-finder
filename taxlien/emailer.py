import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send(cfg, subject: str, html: str) -> None:
    if not (cfg.gmail_user and cfg.gmail_app_password and cfg.email_to):
        print("Email not configured (GMAIL_USER / GMAIL_APP_PASSWORD / EMAIL_TO); skipping send.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_user
    msg["To"] = ", ".join(cfg.email_to)
    msg.attach(MIMEText("This report is HTML; open it in an HTML-capable mail client.", "plain"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(cfg.gmail_user, cfg.gmail_app_password)
        smtp.sendmail(cfg.gmail_user, cfg.email_to, msg.as_string())
    print("Email sent.")
