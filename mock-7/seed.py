"""Starting data, so the app is worth looking at the moment it boots.

Two accounts on purpose: `acct_acme` and `acct_globex` both own templates and
messages, which is what makes a cross-tenant read observable by hand.
"""

from __future__ import annotations

from app.store import store

ACME = "acct_acme"
GLOBEX = "acct_globex"


def seed():
    store.reset()

    welcome = store.create(
        "templates",
        {
            "account_id": ACME,
            "name": "welcome",
            "subject": "Welcome to Acme",
            "body": "Hi {{name}}, thanks for signing up.",
        },
    )
    store.create(
        "templates",
        {
            "account_id": ACME,
            "name": "password-reset",
            "subject": "Reset your password",
            "body": "Use {{link}} within 30 minutes.",
        },
    )
    globex_tpl = store.create(
        "templates",
        {
            "account_id": GLOBEX,
            "name": "invoice",
            "subject": "Your invoice",
            "body": "Invoice {{number}} is ready.",
        },
    )

    delivered = store.create(
        "messages",
        {
            "account_id": ACME,
            "to": "ada@example.com",
            "subject": "Welcome to Acme",
            "template_id": welcome["id"],
            "template_name": welcome["name"],
            "variables": {"name": "Ada"},
            "status": "queued",
            "attempts": 0,
            "last_error": None,
        },
    )
    store.update("messages", delivered["id"], {"status": "sending", "attempts": 1})
    store.update("messages", delivered["id"], {"status": "delivered"})
    store.create(
        "events",
        {
            "account_id": ACME,
            "message_id": delivered["id"],
            "type": "message.delivered",
            "detail": None,
        },
    )

    store.create(
        "messages",
        {
            "account_id": ACME,
            "to": "grace@example.com",
            "subject": "Reset your password",
            "template_id": welcome["id"],
            "template_name": welcome["name"],
            "variables": {"link": "https://acme.test/r/abc"},
            "status": "queued",
            "attempts": 0,
            "last_error": None,
        },
    )

    store.create(
        "messages",
        {
            "account_id": GLOBEX,
            "to": "finance@globex.test",
            "subject": "Your invoice",
            "template_id": globex_tpl["id"],
            "template_name": globex_tpl["name"],
            "variables": {"number": "INV-77"},
            "status": "queued",
            "attempts": 0,
            "last_error": None,
        },
    )
