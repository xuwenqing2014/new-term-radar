"""Mail a report (Markdown) through Resend or SMTP. Optional.

Environment: RESEND_API_KEY, EMAIL_FROM (an address on a domain you verified in Resend),
RADAR_EMAIL_TO (comma-separated for several). Without RADAR_EMAIL_TO it says so and does
nothing: a missing recipient must not fail the run that produced the report.
Needs the `markdown` package (pip install markdown).
SMTP: RADAR_MAIL_BACKEND=smtp and TRENDS_SMTP_SERVER/PORT,
TRENDS_SENDER_EMAIL/PASSWORD, TRENDS_RECIPIENT_EMAIL. RADAR_SEND_EMAIL=true
automatically mails reports from daily/sources without --mail.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from . import UA

# Inline styles: Gmail drops a <style> block that sits outside <head>, which leaves
# tables unbordered with centred headers. Every tag carries its own.
FONT = "-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif"
INLINE = {
    "h2": "font-size:18px;margin:0 0 12px;color:#1f2328",
    "h3": "font-size:15px;margin:24px 0 8px;color:#1f2328",
    "p": "margin:8px 0",
    "table": "border-collapse:collapse;margin:8px 0;width:100%",
    "th": "border:1px solid #d0d7de;padding:6px 10px;font-size:13px;background:#f6f8fa;text-align:left;white-space:nowrap",
    "td": "border:1px solid #d0d7de;padding:6px 10px;font-size:13px;vertical-align:top",
    "ul": "padding-left:0;margin:8px 0;list-style:none",
    "li": "margin:0 0 10px;padding:8px 12px;border-left:3px solid #d0d7de;background:#f6f8fa;font-size:13px",
    "a": "color:#0969da",
}
TAG = re.compile(r"<(" + "|".join(INLINE) + r")(\s[^>]*)?>")
_ADDRESS = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[a-z]{2,}$", re.I)


def _inline(m: re.Match) -> str:
    attrs = m.group(2) or ""
    if m.group(1) == "td" and 'style="text-align' in attrs:   # markdown's own column alignment
        return f"<td{attrs}>"
    return f'<{m.group(1)} style="{INLINE[m.group(1)]}"{attrs}>'


def html(md_text: str) -> str:
    import markdown
    body = TAG.sub(_inline, markdown.markdown(md_text, extensions=["tables"]))
    return f'<div style="font:14px/1.6 {FONT};color:#1f2328;max-width:900px">{body}</div>'


def recipients(raw: str) -> list[str]:
    """RADAR_EMAIL_TO as people type it: commas (also full-width), semicolons or spaces
    between addresses; quotes and angle brackets around them are dropped."""
    parts = re.split(r"[,，;；\s]+", raw.strip().strip("\"'"))
    return [p.strip("\"'<>") for p in parts if p.strip("\"'<>")]


def enabled() -> bool:
    return os.environ.get("RADAR_SEND_EMAIL", "").lower() in {"1", "true", "yes"}


def _smtp(subject: str, md_text: str, to: list[str]) -> str:
    sender = os.environ["TRENDS_SENDER_EMAIL"]
    password = os.environ["TRENDS_SENDER_PASSWORD"]
    host = os.environ.get("TRENDS_SMTP_SERVER", "smtp.gmail.com")
    port = int(os.environ.get("TRENDS_SMTP_PORT", "587"))
    use_ssl = os.environ.get("TRENDS_SMTP_USE_SSL", str(port == 465)).lower() in {"1", "true", "yes"}
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject
    msg.set_content(md_text)
    msg.add_alternative(html(md_text), subtype="html")
    msg.add_attachment(md_text.encode("utf-8"), maintype="text", subtype="markdown", filename="radar-report.md")
    context = ssl.create_default_context()
    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    kwargs = {"context": context} if use_ssl else {}
    with smtp_cls(host, port, timeout=30, **kwargs) as server:
        if not use_ssl:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(sender, password)
        refused = server.send_message(msg, from_addr=sender, to_addrs=to)
        if refused:
            raise RuntimeError(f"mail delivery rejected for {len(refused)} recipient(s)")
    return "mail sent (SMTP accepted)"


def send(subject: str, md_text: str) -> str:
    smtp = os.environ.get("RADAR_MAIL_BACKEND", "resend").lower() == "smtp"
    to = recipients(os.environ.get("RADAR_EMAIL_TO") or (os.environ.get("TRENDS_RECIPIENT_EMAIL", "") if smtp else ""))
    if not to:
        return "mail skipped: RADAR_EMAIL_TO not set"
    bad = [i + 1 for i, a in enumerate(to) if not _ADDRESS.match(a)]
    if bad:   # the address itself stays out of the log
        raise SystemExit(f"RADAR_EMAIL_TO: {len(to)} address(es), number {bad} is not an email address")
    if smtp:
        return _smtp(subject, md_text, to)
    body = json.dumps({"from": os.environ["EMAIL_FROM"], "to": to, "subject": subject, "html": html(md_text)}).encode()
    # Resend sits behind Cloudflare, which answers Python's default User-Agent with 403 "error code: 1010".
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}", "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return f"mail sent ({res.status})"
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Resend {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
