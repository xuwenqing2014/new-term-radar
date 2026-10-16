from unittest.mock import MagicMock

import pytest

from radar import config, notify
from radar.__main__ import _finish
from types import SimpleNamespace


@pytest.mark.parametrize("port", [465, 587])
def test_smtp_transport_and_report(monkeypatch, port):
    values = {
        "RADAR_MAIL_BACKEND": "smtp", "RADAR_EMAIL_TO": "",
        "TRENDS_RECIPIENT_EMAIL": "reader@example.com; second@example.com",
        "TRENDS_SENDER_EMAIL": "sender@example.com", "TRENDS_SENDER_PASSWORD": "secret",
        "TRENDS_SMTP_SERVER": "smtp.example.com", "TRENDS_SMTP_PORT": str(port),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TRENDS_SMTP_USE_SSL", raising=False)
    transport = MagicMock()
    server = transport.return_value.__enter__.return_value
    server.send_message.return_value = {}
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL" if port == 465 else "SMTP", transport)
    assert notify.send("日报", "## 今日报告\n\n内容") == "mail sent (SMTP accepted)"
    assert server.starttls.called == (port == 587)
    server.login.assert_called_once_with("sender@example.com", "secret")
    args, kwargs = server.send_message.call_args
    assert kwargs["to_addrs"] == ["reader@example.com", "second@example.com"]
    msg = args[0]
    assert msg["Subject"] == "日报"
    assert "内容" in msg.get_body(preferencelist=("html",)).get_content()
    assert next(msg.iter_attachments()).get_filename() == "radar-report.md"


def test_local_env_enables_automatic_mail(tmp_path, monkeypatch):
    monkeypatch.delenv("RADAR_SEND_EMAIL", raising=False)
    monkeypatch.setenv("EXISTING_VALUE", "process")
    env = tmp_path / ".env"
    env.write_text("RADAR_SEND_EMAIL=true\nEXISTING_VALUE=file\n")
    config.load_env(env)
    monkeypatch.setenv("RADAR_SEND_EMAIL", "true")
    import os
    assert os.environ["EXISTING_VALUE"] == "process"
    send = MagicMock(return_value="mail sent")
    monkeypatch.setattr(notify, "send", send)
    _finish(SimpleNamespace(out=str(tmp_path / "report.md"), mail=False), "report", "subject")
    send.assert_called_once_with("subject", "report")
